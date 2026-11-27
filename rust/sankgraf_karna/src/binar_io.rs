// Copyright (C) Floden-projektet. Licens: GPL-3.0-or-later.
//! Kompakt binärt tempfilformat (`.flsg`) med magisk header, version, dimensioner,
//! längdkontroll och IEEE CRC-32 kontrollsumma. Inga externa beroenden.

use std::fs::File;
use std::io::{BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::Path;

pub const MAGIC_IN: [u8; 8] = *b"FLSG_IN\0";
pub const MAGIC_UT: [u8; 8] = *b"FLSG_UT\0";
pub const FORMAT_VERSION: u32 = 1;
pub const HEADER_IN_BYTES: u64 = 48;
pub const HEADER_UT_BYTES: u64 = 64;

const SKRIV_CHUNK_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lage {
    Komplett = 0,
    Avrinning = 1,
    Granspar = 2,
    Avrinningsnat = 3,
    AvrinningsnatTillrinning = 4,
}

impl Lage {
    pub fn fran_u32(v: u32) -> Result<Self, String> {
        match v {
            0 => Ok(Self::Komplett),
            1 => Ok(Self::Avrinning),
            2 => Ok(Self::Granspar),
            3 => Ok(Self::Avrinningsnat),
            4 => Ok(Self::AvrinningsnatTillrinning),
            _ => Err(format!("Okänt körläge i header: {v}")),
        }
    }

    pub fn som_u32(self) -> u32 {
        self as u32
    }
}

#[derive(Debug, Clone)]
pub enum ZData {
    F32(Vec<f32>),
    F64(Vec<f64>),
}

impl ZData {
    #[inline(always)]
    pub fn get_f64(&self, idx: usize) -> f64 {
        match self {
            Self::F32(v) => v[idx] as f64,
            Self::F64(v) => v[idx],
        }
    }
}

pub struct Indata {
    pub lage: Lage,
    pub hojd: usize,
    pub bredd: usize,
    pub z: ZData,
    pub aktiv: Vec<u8>,
    pub catch: Option<Vec<i32>>,
    pub egen: Option<Vec<f64>>,
    /// Sorterade kantbarriärer med unik nyckel `((u as u64) << 32) | (v as u64)`, där `u < v`.
    pub barriarer: Vec<(u64, f64)>,
}

const CRC32_TABELL: [u32; 256] = {
    let mut tab = [0u32; 256];
    let mut i = 0u32;
    while i < 256 {
        let mut c = i;
        let mut k = 0;
        while k < 8 {
            if c & 1 != 0 {
                c = 0xEDB8_8320 ^ (c >> 1);
            } else {
                c >>= 1;
            }
            k += 1;
        }
        tab[i as usize] = c;
        i += 1;
    }
    tab
};

/// Uppdaterar en strömmande IEEE CRC-32 (kompatibel med Pythons `zlib.crc32`).
pub fn crc32_uppdatera(mut crc: u32, data: &[u8]) -> u32 {
    crc = !crc;
    for &b in data {
        let idx = ((crc ^ (b as u32)) & 0xFF) as usize;
        crc = CRC32_TABELL[idx] ^ (crc >> 8);
    }
    !crc
}

fn las_u32(buf: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([buf[off], buf[off + 1], buf[off + 2], buf[off + 3]])
}

fn las_u64(buf: &[u8], off: usize) -> u64 {
    u64::from_le_bytes([
        buf[off],
        buf[off + 1],
        buf[off + 2],
        buf[off + 3],
        buf[off + 4],
        buf[off + 5],
        buf[off + 6],
        buf[off + 7],
    ])
}

fn las_exakt_med_crc<R: Read>(
    lasare: &mut R,
    buf: &mut [u8],
    crc: &mut u32,
    kontext: &str,
) -> Result<(), String> {
    lasare
        .read_exact(buf)
        .map_err(|e| format!("Kunde inte läsa {kontext}: {e}"))?;
    *crc = crc32_uppdatera(*crc, buf);
    Ok(())
}

fn las_f32_vec<R: Read>(
    lasare: &mut R,
    antal: usize,
    crc: &mut u32,
    kontext: &str,
) -> Result<Vec<f32>, String> {
    let mut ut = Vec::with_capacity(antal);
    let mut chunk = vec![0u8; SKRIV_CHUNK_BYTES];
    let mut kvar = antal;
    while kvar > 0 {
        let ta = kvar.min(SKRIV_CHUNK_BYTES / 4);
        let del = &mut chunk[..ta * 4];
        las_exakt_med_crc(lasare, del, crc, kontext)?;
        for b in del.chunks_exact(4) {
            ut.push(f32::from_le_bytes([b[0], b[1], b[2], b[3]]));
        }
        kvar -= ta;
    }
    Ok(ut)
}

fn las_f64_vec<R: Read>(
    lasare: &mut R,
    antal: usize,
    crc: &mut u32,
    kontext: &str,
) -> Result<Vec<f64>, String> {
    let mut ut = Vec::with_capacity(antal);
    let mut chunk = vec![0u8; SKRIV_CHUNK_BYTES];
    let mut kvar = antal;
    while kvar > 0 {
        let ta = kvar.min(SKRIV_CHUNK_BYTES / 8);
        let del = &mut chunk[..ta * 8];
        las_exakt_med_crc(lasare, del, crc, kontext)?;
        for b in del.chunks_exact(8) {
            ut.push(f64::from_le_bytes([
                b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
            ]));
        }
        kvar -= ta;
    }
    Ok(ut)
}

fn las_i32_vec<R: Read>(
    lasare: &mut R,
    antal: usize,
    crc: &mut u32,
    kontext: &str,
) -> Result<Vec<i32>, String> {
    let mut ut = Vec::with_capacity(antal);
    let mut chunk = vec![0u8; SKRIV_CHUNK_BYTES];
    let mut kvar = antal;
    while kvar > 0 {
        let ta = kvar.min(SKRIV_CHUNK_BYTES / 4);
        let del = &mut chunk[..ta * 4];
        las_exakt_med_crc(lasare, del, crc, kontext)?;
        for b in del.chunks_exact(4) {
            ut.push(i32::from_le_bytes([b[0], b[1], b[2], b[3]]));
        }
        kvar -= ta;
    }
    Ok(ut)
}

pub fn las_indata(sokvag: &Path) -> Result<Indata, String> {
    let fil = File::open(sokvag)
        .map_err(|e| format!("Kunde inte öppna indatafil {}: {e}", sokvag.display()))?;
    let fil_langd = fil
        .metadata()
        .map_err(|e| format!("Kunde inte läsa filstorlek för {}: {e}", sokvag.display()))?
        .len();
    if fil_langd < HEADER_IN_BYTES {
        return Err(format!(
            "Indatafilen {} är trunkerad ({fil_langd} byte < {HEADER_IN_BYTES} byte header).",
            sokvag.display()
        ));
    }

    let mut lasare = BufReader::new(fil);
    let mut hdr = [0u8; HEADER_IN_BYTES as usize];
    lasare
        .read_exact(&mut hdr)
        .map_err(|e| format!("Kunde inte läsa indata-header: {e}"))?;

    if hdr[0..8] != MAGIC_IN {
        return Err("Ogiltig magisk signatur i indatafil (förväntade FLSG_IN).".to_string());
    }
    let version = las_u32(&hdr, 8);
    if version != FORMAT_VERSION {
        return Err(format!(
            "Okänd formatversion {version} i indatafil (stöds: {FORMAT_VERSION})."
        ));
    }
    let lage = Lage::fran_u32(las_u32(&hdr, 12))?;
    let z_typ = las_u32(&hdr, 16);
    if z_typ != 0 && z_typ != 1 {
        return Err(format!("Ogiltig z_typ {z_typ} i indatafil (förväntade 0=f32 eller 1=f64)."));
    }
    let hojd_u32 = las_u32(&hdr, 20);
    let bredd_u32 = las_u32(&hdr, 24);
    let header_crc = las_u32(&hdr, 28);
    let antal_barriarer_u64 = las_u64(&hdr, 32);
    let header_payload_langd = las_u64(&hdr, 40);

    let hojd = hojd_u32 as usize;
    let bredd = bredd_u32 as usize;
    let n_celler = hojd
        .checked_mul(bredd)
        .ok_or_else(|| format!("Rutnätsstorlek {hojd}x{bredd} svämmade över usize."))?;
    if n_celler >= i32::MAX as usize {
        return Err(format!(
            "Rutnätet ({hojd}x{bredd} = {n_celler} celler) överskrider i32::MAX."
        ));
    }
    let n_u64 = n_celler as u64;
    let z_elem_bytes: u64 = if z_typ == 0 { 4 } else { 8 };
    let z_bytes = n_u64
        .checked_mul(z_elem_bytes)
        .ok_or_else(|| "Byte-längd för z svämmade över.".to_string())?;
    let catch_bytes = if lage == Lage::Granspar {
        n_u64
            .checked_mul(4)
            .ok_or_else(|| "Byte-längd för catch svämmade över.".to_string())?
    } else {
        0
    };
    let egen_bytes = if lage == Lage::AvrinningsnatTillrinning {
        n_u64
            .checked_mul(8)
            .ok_or_else(|| "Byte-längd för egen_yta svämmade över.".to_string())?
    } else {
        0
    };
    let barriar_bytes = antal_barriarer_u64
        .checked_mul(16)
        .ok_or_else(|| "Byte-längd för barriärer svämmade över.".to_string())?;
    let forvantad_payload = z_bytes
        .checked_add(n_u64)
        .and_then(|x| x.checked_add(catch_bytes))
        .and_then(|x| x.checked_add(egen_bytes))
        .and_then(|x| x.checked_add(barriar_bytes))
        .ok_or_else(|| "Total payload-längd svämmade över.".to_string())?;

    if header_payload_langd != forvantad_payload {
        return Err(format!(
            "Headerns payload-längd ({header_payload_langd}) matchar inte dimensionerna ({forvantad_payload})."
        ));
    }
    let faktisk_payload = fil_langd - HEADER_IN_BYTES;
    if faktisk_payload != forvantad_payload {
        return Err(format!(
            "Korrupt eller trunkerad indatafil: fick {faktisk_payload} payload-byte, väntade {forvantad_payload}."
        ));
    }

    let antal_barriarer = usize::try_from(antal_barriarer_u64)
        .map_err(|_| "antal_barriarer passar inte i usize.".to_string())?;

    let mut crc = 0u32;
    let z = if z_typ == 0 {
        ZData::F32(las_f32_vec(&mut lasare, n_celler, &mut crc, "z (f32)")?)
    } else {
        ZData::F64(las_f64_vec(&mut lasare, n_celler, &mut crc, "z (f64)")?)
    };

    let mut aktiv = vec![0u8; n_celler];
    if n_celler > 0 {
        las_exakt_med_crc(&mut lasare, &mut aktiv, &mut crc, "aktiv")?;
    }

    let catch = if lage == Lage::Granspar {
        Some(las_i32_vec(&mut lasare, n_celler, &mut crc, "catch")?)
    } else {
        None
    };

    let egen = if lage == Lage::AvrinningsnatTillrinning {
        Some(las_f64_vec(&mut lasare, n_celler, &mut crc, "egen_yta")?)
    } else {
        None
    };

    let mut barriarer: Vec<(u64, f64)> = Vec::with_capacity(antal_barriarer);
    if antal_barriarer > 0 {
        let b_u = las_i32_vec(&mut lasare, antal_barriarer, &mut crc, "barriar_u")?;
        let b_v = las_i32_vec(&mut lasare, antal_barriarer, &mut crc, "barriar_v")?;
        let b_w = las_f64_vec(&mut lasare, antal_barriarer, &mut crc, "barriar_w")?;
        let n_i32 = n_celler as i32;
        for i in 0..antal_barriarer {
            let u = b_u[i];
            let v = b_v[i];
            let w = b_w[i];
            if u < 0 || v <= u || v >= n_i32 {
                return Err(format!(
                    "Ogiltig barriärkant #{i}: ({u}, {v}) utanför [0, {n_i32}) eller u >= v."
                ));
            }
            if !w.is_finite() {
                return Err(format!("Icke-ändlig barriärhöjd på kant #{i}: {w}."));
            }
            let nyckel = ((u as u64) << 32) | (v as u64);
            barriarer.push((nyckel, w));
        }
        barriarer.sort_unstable_by_key(|&(k, _)| k);
        for par in barriarer.windows(2) {
            if par[0].0 == par[1].0 {
                return Err("Duplicerad kantnyckel i kantbarriärer.".to_string());
            }
        }
    }

    if crc != header_crc {
        return Err(format!(
            "CRC-32-fel i indatafil: beräknade {crc:#010x}, header angav {header_crc:#010x}."
        ));
    }

    Ok(Indata {
        lage,
        hojd,
        bredd,
        z,
        aktiv,
        catch,
        egen,
        barriarer,
    })
}

pub struct UtSkrivare {
    skrivare: BufWriter<File>,
    crc: u32,
    payload_langd: u64,
    chunk: Vec<u8>,
}

impl UtSkrivare {
    pub fn skapa(sokvag: &Path) -> Result<Self, String> {
        let fil = File::create(sokvag)
            .map_err(|e| format!("Kunde inte skapa utdatafil {}: {e}", sokvag.display()))?;
        let mut skrivare = BufWriter::new(fil);
        let tomt_hdr = [0u8; HEADER_UT_BYTES as usize];
        skrivare
            .write_all(&tomt_hdr)
            .map_err(|e| format!("Kunde inte skriva header-platshållare: {e}"))?;
        Ok(Self {
            skrivare,
            crc: 0,
            payload_langd: 0,
            chunk: Vec::with_capacity(SKRIV_CHUNK_BYTES),
        })
    }

    fn skriv_ra(&mut self, bytes: &[u8]) -> Result<(), String> {
        if bytes.is_empty() {
            return Ok(());
        }
        self.skrivare
            .write_all(bytes)
            .map_err(|e| format!("Skrivfel till utdatafil: {e}"))?;
        self.crc = crc32_uppdatera(self.crc, bytes);
        self.payload_langd = self
            .payload_langd
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| "Utdata-payload svämmade över u64.".to_string())?;
        Ok(())
    }

    pub fn skriv_i32_skiva(&mut self, data: &[i32]) -> Result<(), String> {
        let max_elem = SKRIV_CHUNK_BYTES / 4;
        for del in data.chunks(max_elem) {
            self.chunk.clear();
            for &v in del {
                self.chunk.extend_from_slice(&v.to_le_bytes());
            }
            let bytes = std::mem::take(&mut self.chunk);
            let res = self.skriv_ra(&bytes);
            self.chunk = bytes;
            res?;
        }
        Ok(())
    }

    pub fn skriv_i64_skiva(&mut self, data: &[i64]) -> Result<(), String> {
        let max_elem = SKRIV_CHUNK_BYTES / 8;
        for del in data.chunks(max_elem) {
            self.chunk.clear();
            for &v in del {
                self.chunk.extend_from_slice(&v.to_le_bytes());
            }
            let bytes = std::mem::take(&mut self.chunk);
            let res = self.skriv_ra(&bytes);
            self.chunk = bytes;
            res?;
        }
        Ok(())
    }

    pub fn skriv_f64_skiva(&mut self, data: &[f64]) -> Result<(), String> {
        let max_elem = SKRIV_CHUNK_BYTES / 8;
        for del in data.chunks(max_elem) {
            self.chunk.clear();
            for &v in del {
                self.chunk.extend_from_slice(&v.to_le_bytes());
            }
            let bytes = std::mem::take(&mut self.chunk);
            let res = self.skriv_ra(&bytes);
            self.chunk = bytes;
            res?;
        }
        Ok(())
    }

    pub fn avsluta(
        mut self,
        lage: Lage,
        hojd: usize,
        bredd: usize,
        antal_lov: i32,
        antal_par: usize,
        antal_kanter: usize,
    ) -> Result<(), String> {
        let hojd_u32 =
            u32::try_from(hojd).map_err(|_| "hojd passar inte i u32.".to_string())?;
        let bredd_u32 =
            u32::try_from(bredd).map_err(|_| "bredd passar inte i u32.".to_string())?;
        let lov_u32 =
            u32::try_from(antal_lov).map_err(|_| "antal_lov passar inte i u32.".to_string())?;

        let mut hdr = [0u8; HEADER_UT_BYTES as usize];
        hdr[0..8].copy_from_slice(&MAGIC_UT);
        hdr[8..12].copy_from_slice(&FORMAT_VERSION.to_le_bytes());
        hdr[12..16].copy_from_slice(&lage.som_u32().to_le_bytes());
        hdr[16..20].copy_from_slice(&hojd_u32.to_le_bytes());
        hdr[20..24].copy_from_slice(&bredd_u32.to_le_bytes());
        hdr[24..28].copy_from_slice(&lov_u32.to_le_bytes());
        hdr[28..32].copy_from_slice(&self.crc.to_le_bytes());
        hdr[32..40].copy_from_slice(&(antal_par as u64).to_le_bytes());
        hdr[40..48].copy_from_slice(&(antal_kanter as u64).to_le_bytes());
        hdr[48..56].copy_from_slice(&self.payload_langd.to_le_bytes());
        hdr[56..64].copy_from_slice(&0u64.to_le_bytes());

        self.skrivare
            .seek(SeekFrom::Start(0))
            .map_err(|e| format!("Kunde inte spola tillbaka utdatafilen: {e}"))?;
        self.skrivare
            .write_all(&hdr)
            .map_err(|e| format!("Kunde inte skriva utdata-header: {e}"))?;
        self.skrivare
            .flush()
            .map_err(|e| format!("Kunde inte tömma utdatabuffert: {e}"))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unik_tempfil(namn: &str) -> std::path::PathBuf {
        let ns = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("klocka")
            .as_nanos();
        std::env::temp_dir().join(format!("floden_sankgraf_test_{namn}_{}_{ns}.flsg", std::process::id()))
    }

    fn bygg_infil_bytes(
        lage: u32,
        z_typ: u32,
        hojd: u32,
        bredd: u32,
        z_bytes: &[u8],
        aktiv: &[u8],
        catch: &[i32],
        barriarer: &[(i32, i32, f64)],
    ) -> Vec<u8> {
        let mut payload = Vec::new();
        payload.extend_from_slice(z_bytes);
        payload.extend_from_slice(aktiv);
        if lage == 2 {
            for &c in catch {
                payload.extend_from_slice(&c.to_le_bytes());
            }
        }
        if !barriarer.is_empty() {
            for &(u, _, _) in barriarer {
                payload.extend_from_slice(&u.to_le_bytes());
            }
            for &(_, v, _) in barriarer {
                payload.extend_from_slice(&v.to_le_bytes());
            }
            for &(_, _, w) in barriarer {
                payload.extend_from_slice(&w.to_le_bytes());
            }
        }
        let crc = crc32_uppdatera(0, &payload);
        let mut fil = Vec::with_capacity(HEADER_IN_BYTES as usize + payload.len());
        fil.extend_from_slice(&MAGIC_IN);
        fil.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
        fil.extend_from_slice(&lage.to_le_bytes());
        fil.extend_from_slice(&z_typ.to_le_bytes());
        fil.extend_from_slice(&hojd.to_le_bytes());
        fil.extend_from_slice(&bredd.to_le_bytes());
        fil.extend_from_slice(&crc.to_le_bytes());
        fil.extend_from_slice(&(barriarer.len() as u64).to_le_bytes());
        fil.extend_from_slice(&(payload.len() as u64).to_le_bytes());
        fil.extend_from_slice(&payload);
        fil
    }

    #[test]
    fn crc32_matchar_ieee_standardvektor() {
        assert_eq!(crc32_uppdatera(0, b"123456789"), 0xCBF4_3926);
        // Strömmande uppdatering i delar ger exakt samma summa
        let del1 = crc32_uppdatera(0, b"1234");
        let del2 = crc32_uppdatera(del1, b"56789");
        assert_eq!(del2, 0xCBF4_3926);
    }

    #[test]
    fn indata_lasning_och_korruptionskontroller() {
        let sokvag = unik_tempfil("io_check");
        let z_vals: [f32; 4] = [10.0, 10.5, 11.0, 9.5];
        let mut z_bytes = Vec::new();
        for v in z_vals {
            z_bytes.extend_from_slice(&v.to_le_bytes());
        }
        let aktiv = [1u8, 1, 1, 1];
        let barriarer = [(0i32, 1i32, 12.25f64)];
        let giltig = bygg_infil_bytes(0, 0, 2, 2, &z_bytes, &aktiv, &[], &barriarer);

        fs::write(&sokvag, &giltig).expect("skriv giltig");
        let ind = las_indata(&sokvag).expect("ska läsa giltig fil");
        assert_eq!(ind.hojd, 2);
        assert_eq!(ind.bredd, 2);
        assert_eq!(ind.barriarer.len(), 1);
        assert_eq!(ind.z.get_f64(3), 9.5);

        // Korrupt CRC (bitfel i payload)
        let mut korrupt = giltig.clone();
        let sista = korrupt.len() - 1;
        korrupt[sista] ^= 0x01;
        fs::write(&sokvag, &korrupt).expect("skriv korrupt");
        let fel_crc = las_indata(&sokvag).err().expect("ska ge CRC-fel");
        assert!(fel_crc.contains("CRC-32"), "oväntat fel: {fel_crc}");

        // Trunkerad fil
        fs::write(&sokvag, &giltig[..giltig.len() - 3]).expect("skriv trunkerad");
        let fel_trunk = las_indata(&sokvag).err().expect("ska ge längdfel");
        assert!(fel_trunk.contains("trunkerad"), "oväntat fel: {fel_trunk}");

        // Fel magisk signatur
        let mut fel_magi = giltig.clone();
        fel_magi[0] = b'X';
        fs::write(&sokvag, &fel_magi).expect("skriv fel magi");
        let fel_m = las_indata(&sokvag).err().expect("ska ge magifel");
        assert!(fel_m.contains("magisk"), "oväntat fel: {fel_m}");

        let _ = fs::remove_file(&sokvag);
    }
}
