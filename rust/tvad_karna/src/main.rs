//! `tvad_karna <indatakatalog> <utkatalog> [<trådar>]`
//!
//! Flodens 2D-lösare (lokal tröghet) som parallell CPU-kärna. Indata och utdata är `.npy`-filer
//! som Python-omslaget `tvad.rust_backend` skriver och läser; alla flyttal går som binära
//! float64, så inget avrundas på vägen. Se README.md för filerna.

mod karna;
mod losare;
mod npy;

use losare::{Indata, Rand};
use std::path::Path;
use std::process::ExitCode;
use std::time::Instant;

type Fel = Box<dyn std::error::Error>;

fn las_f64(kat: &Path, namn: &str, langd: usize) -> Result<Vec<f64>, Fel> {
    let v = npy::las(&kat.join(namn))?.f64()?;
    if v.len() != langd {
        return Err(format!("{namn}: {} värden, väntade {langd}", v.len()).into());
    }
    Ok(v)
}

fn las_bool(kat: &Path, namn: &str, langd: usize) -> Result<Vec<bool>, Fel> {
    let v = npy::las(&kat.join(namn))?.u8()?;
    if v.len() != langd {
        return Err(format!("{namn}: {} värden, väntade {langd}", v.len()).into());
    }
    Ok(v.into_iter().map(|b| b != 0).collect())
}

fn las_rand(kat: &Path, sida: &str, langd: usize) -> Result<Rand, Fel> {
    Ok(Rand {
        mask: las_bool(kat, &format!("rand_{sida}_mask.npy"), langd)?,
        lutning: las_f64(kat, &format!("rand_{sida}_lutning.npy"), langd)?,
        bredd: las_f64(kat, &format!("rand_{sida}_bredd.npy"), langd)?,
    })
}

fn kor(args: &[String]) -> Result<(), Fel> {
    if args.len() < 2 || args.len() > 3 {
        return Err("Användning: tvad_karna <indatakatalog> <utkatalog> [<trådar>]".into());
    }
    if args.len() == 3 {
        let tradar: usize = args[2].parse()?;
        rayon::ThreadPoolBuilder::new().num_threads(tradar).build_global()?;
    }
    let kat = Path::new(&args[0]);
    let ut = Path::new(&args[1]);
    std::fs::create_dir_all(ut)?;

    let z = npy::las(&kat.join("z_bed.npy"))?;
    if z.shape.len() != 2 {
        return Err("z_bed.npy måste vara 2D".into());
    }
    let (h, w) = (z.shape[0], z.shape[1]);
    if h == 0 || w == 0 {
        return Err("Tomt rutnät".into());
    }
    let n = h * w;
    let p = las_f64(kat, "param.npy", 11)?;
    let tider = npy::las(&kat.join("regn_tider.npy"))?.f64()?;
    let intens = npy::las(&kat.join("regn_intens_m_s.npy"))?.f64()?;
    if tider.len() != intens.len() || tider.len() < 2 {
        return Err("Regnserien måste ha minst två punkter och lika långa vektorer".into());
    }
    let ind = Indata {
        hojd: h,
        bredd: w,
        z_bed: z.f64()?,
        manning: las_f64(kat, "manning.npy", n)?,
        aktiv: las_bool(kat, "aktiv.npy", n)?,
        vast: las_rand(kat, "vast", h)?,
        ost: las_rand(kat, "ost", h)?,
        norr: las_rand(kat, "norr", w)?,
        soder: las_rand(kat, "soder", w)?,
        tider,
        intens_m_s: intens,
        dx: p[0],
        dy: p[1],
        cfl: p[2],
        min_djup: p[3],
        dt_max: p[4],
        theta: p[5],
        t_start: p[6],
        t_slut: p[7],
        total_aktiv_area: p[8],
        total_doman_area: p[9],
        gles_front: p[10] != 0.0,
        regn_area: if kat.join("regn_area.npy").is_file() {
            Some(las_f64(kat, "regn_area.npy", n)?)
        } else {
            None
        },
        tak_ut_area: if kat.join("tak_ut.npy").is_file() {
            las_f64(kat, "tak_ut.npy", 1)?[0]
        } else {
            0.0
        },
    };

    let start = Instant::now();
    let res = losare::kor(&ind);
    let kortid = start.elapsed().as_secs_f64();

    let form = [h, w];
    npy::skriv_f32(&ut.join("max_djup.npy"), &form, &res.max_djup)?;
    npy::skriv_f32(&ut.join("slut_djup.npy"), &form, &res.slut_djup)?;
    npy::skriv_f32(&ut.join("max_hastighet.npy"), &form, &res.max_hastighet)?;
    npy::skriv_f32(&ut.join("fara.npy"), &form, &res.fara)?;
    npy::skriv_f32(&ut.join("tid_till_0_1m.npy"), &form, &res.tid_01)?;
    npy::skriv_f32(&ut.join("tid_till_0_2m.npy"), &form, &res.tid_02)?;
    npy::skriv_f64(&ut.join("volym.npy"), &form, &res.volym)?;
    let matv = [
        res.in_m3,
        res.ut_m3,
        res.antal_steg as f64,
        res.sum_aktiv_andel,
        kortid,
        rayon::current_num_threads() as f64,
    ];
    npy::skriv_f64(&ut.join("matvarden.npy"), &[matv.len()], &matv)?;
    // Läsbar kopia; Rust skriver flyttal med kortaste exakta representation.
    let json = format!(
        "{{\"in_m3\": {:?}, \"ut_m3\": {:?}, \"antal_steg\": {}, \"kortid_s\": {:?}, \"tradar\": {}}}\n",
        res.in_m3,
        res.ut_m3,
        res.antal_steg,
        kortid,
        rayon::current_num_threads()
    );
    std::fs::write(ut.join("matvarden.json"), json)?;
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match kor(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("tvad_karna: {e}");
            ExitCode::FAILURE
        }
    }
}
