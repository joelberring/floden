// Copyright (C) Floden-projektet. Licens: GPL-3.0-or-later.
//! `sankgraf_karna <indata.flsg> <utdata.flsg>`
//!
//! Deterministisk Rust-kärna utan externa crates för SankGrafs tunga rastersteg:
//! - 4-granne avrinning, kantbarriärer, platå-BFS och lövsänkeetikettering
//! - Extraktion och sortering av unika gränspar och sadelkanter

mod binar_io;
mod karna;

use binar_io::{Indata, Lage, UtSkrivare};
use std::path::Path;
use std::process::ExitCode;

fn skriv_granspar(
    skrivare: &mut UtSkrivare,
    gp: &karna::GransparResultat,
) -> Result<(), String> {
    skrivare.skriv_f64_skiva(&gp.par_w)?;
    skrivare.skriv_i32_skiva(&gp.par_c_lo)?;
    skrivare.skriv_i32_skiva(&gp.par_c_hi)?;
    skrivare.skriv_i64_skiva(&gp.par_start)?;
    skrivare.skriv_i64_skiva(&gp.par_slut)?;
    skrivare.skriv_i32_skiva(&gp.f_u)?;
    skrivare.skriv_i32_skiva(&gp.f_v)?;
    skrivare.skriv_i32_skiva(&gp.f_cu)?;
    skrivare.skriv_i32_skiva(&gp.f_cv)?;
    Ok(())
}

fn kor(args: &[String]) -> Result<(), String> {
    if args.len() == 1 {
        match args[0].as_str() {
            "--version" | "-V" => {
                println!("sankgraf_karna {}", env!("CARGO_PKG_VERSION"));
                return Ok(());
            }
            "--help" | "-h" => {
                println!("Användning: sankgraf_karna <indata.flsg> <utdata.flsg>");
                return Ok(());
            }
            _ => {}
        }
    }
    if args.len() != 2 {
        return Err("Användning: sankgraf_karna <indata.flsg> <utdata.flsg>".to_string());
    }

    let in_sokvag = Path::new(&args[0]);
    let ut_sokvag = Path::new(&args[1]);
    let Indata {
        lage,
        hojd,
        bredd,
        z,
        aktiv,
        catch,
        barriarer,
    } = binar_io::las_indata(in_sokvag)?;

    let mut skrivare = UtSkrivare::skapa(ut_sokvag)?;

    match lage {
        Lage::Komplett => {
            let avr = karna::berakna_avrinningsomraden(hojd, bredd, &z, &aktiv, &barriarer)?;
            let antal_lov = avr.antal_lov;
            let flow_to = avr.flow_to;
            let cell_till_nod = avr.cell_till_nod;

            skrivare.skriv_i32_skiva(&flow_to)?;
            drop(flow_to);
            skrivare.skriv_i32_skiva(&cell_till_nod)?;

            if antal_lov > 0 {
                let gp = karna::bygg_granspar(
                    hojd,
                    bredd,
                    &cell_till_nod,
                    &aktiv,
                    &z,
                    &barriarer,
                )?;
                drop(cell_till_nod);
                drop(aktiv);
                drop(z);
                drop(barriarer);
                skriv_granspar(&mut skrivare, &gp)?;
                skrivare.avsluta(lage, hojd, bredd, antal_lov, gp.par_w.len(), gp.f_u.len())?;
            } else {
                drop(cell_till_nod);
                drop(aktiv);
                drop(z);
                drop(barriarer);
                skrivare.avsluta(lage, hojd, bredd, 0, 0, 0)?;
            }
        }
        Lage::Avrinning => {
            let avr = karna::berakna_avrinningsomraden(hojd, bredd, &z, &aktiv, &barriarer)?;
            drop(aktiv);
            drop(z);
            drop(barriarer);
            skrivare.skriv_i32_skiva(&avr.flow_to)?;
            skrivare.skriv_i32_skiva(&avr.cell_till_nod)?;
            skrivare.avsluta(lage, hojd, bredd, avr.antal_lov, 0, 0)?;
        }
        Lage::Granspar => {
            let catch_vec = catch
                .ok_or_else(|| "catch-data saknas för körläge GRANSPAR.".to_string())?;
            let gp = karna::bygg_granspar(hojd, bredd, &catch_vec, &aktiv, &z, &barriarer)?;
            drop(catch_vec);
            drop(aktiv);
            drop(z);
            drop(barriarer);
            skriv_granspar(&mut skrivare, &gp)?;
            skrivare.avsluta(lage, hojd, bredd, 0, gp.par_w.len(), gp.f_u.len())?;
        }
    }

    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match kor(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("sankgraf_karna: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::binar_io::{
        crc32_uppdatera, FORMAT_VERSION, HEADER_IN_BYTES, HEADER_UT_BYTES, MAGIC_IN, MAGIC_UT,
    };
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_sokvag(namn: &str) -> std::path::PathBuf {
        let ns = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("klocka")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "floden_sankgraf_e2e_{namn}_{}_{ns}.flsg",
            std::process::id()
        ))
    }

    fn skriv_test_indata(
        sokvag: &Path,
        lage: u32,
        hojd: u32,
        bredd: u32,
        z: &[f32],
        aktiv: &[u8],
        catch: &[i32],
    ) {
        let mut payload = Vec::new();
        for &v in z {
            payload.extend_from_slice(&v.to_le_bytes());
        }
        payload.extend_from_slice(aktiv);
        if lage == 2 {
            for &c in catch {
                payload.extend_from_slice(&c.to_le_bytes());
            }
        }
        let crc = crc32_uppdatera(0, &payload);
        let mut buf = Vec::with_capacity(HEADER_IN_BYTES as usize + payload.len());
        buf.extend_from_slice(&MAGIC_IN);
        buf.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
        buf.extend_from_slice(&lage.to_le_bytes());
        buf.extend_from_slice(&0u32.to_le_bytes()); // f32
        buf.extend_from_slice(&hojd.to_le_bytes());
        buf.extend_from_slice(&bredd.to_le_bytes());
        buf.extend_from_slice(&crc.to_le_bytes());
        buf.extend_from_slice(&0u64.to_le_bytes()); // 0 barriärer
        buf.extend_from_slice(&(payload.len() as u64).to_le_bytes());
        buf.extend_from_slice(&payload);
        fs::write(sokvag, &buf).expect("skriv test-indata");
    }

    #[test]
    fn kor_komplett_avrinning_och_granspar_med_crc() {
        // 5x5 skål: kant=12, mitt 3x3=10 -> 1 lövsänka, 1 gränspar (-1, 0) vid 12.0
        let mut z = vec![12.0f32; 25];
        for r in 1..4 {
            for c in 1..4 {
                z[r * 5 + c] = 10.0;
            }
        }
        let aktiv = vec![1u8; 25];

        let in_fil = temp_sokvag("komplett_in");
        let ut_fil = temp_sokvag("komplett_ut");
        skriv_test_indata(&in_fil, 0, 5, 5, &z, &aktiv, &[]);

        kor(&[
            in_fil.to_string_lossy().into_owned(),
            ut_fil.to_string_lossy().into_owned(),
        ])
        .expect("kor komplett");

        let ut_bytes = fs::read(&ut_fil).expect("läs utfil");
        assert!(ut_bytes.len() >= HEADER_UT_BYTES as usize);
        assert_eq!(&ut_bytes[0..8], &MAGIC_UT);
        let antal_lov = u32::from_le_bytes(ut_bytes[24..28].try_into().expect("lov"));
        let hdr_crc = u32::from_le_bytes(ut_bytes[28..32].try_into().expect("crc"));
        let antal_par = u64::from_le_bytes(ut_bytes[32..40].try_into().expect("par"));
        let payload_len = u64::from_le_bytes(ut_bytes[48..56].try_into().expect("len"));
        assert_eq!(antal_lov, 1);
        assert_eq!(antal_par, 1);
        let payload = &ut_bytes[HEADER_UT_BYTES as usize..];
        assert_eq!(payload.len() as u64, payload_len);
        assert_eq!(crc32_uppdatera(0, payload), hdr_crc);

        // Testa även --version och --help
        kor(&["--version".to_string()]).expect("version");
        kor(&["--help".to_string()]).expect("help");

        let _ = fs::remove_file(&in_fil);
        let _ = fs::remove_file(&ut_fil);
    }
}
