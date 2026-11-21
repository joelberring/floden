//! Minimal läsare och skrivare för NumPy `.npy` (version 1.0, C-ordning, little endian).
//! Stöder `<f8`, `<f4` och `|u1`, vilket är allt som utbytet med Python behöver.

use std::fs;
use std::io::Write;
use std::path::Path;

pub struct Npy {
    pub descr: String,
    pub shape: Vec<usize>,
    pub data: Vec<u8>,
}

fn fel(msg: String) -> Box<dyn std::error::Error> {
    msg.into()
}

pub fn las(sokvag: &Path) -> Result<Npy, Box<dyn std::error::Error>> {
    let bytes = fs::read(sokvag).map_err(|e| fel(format!("{}: {e}", sokvag.display())))?;
    if bytes.len() < 10 || &bytes[0..6] != b"\x93NUMPY" {
        return Err(fel(format!("{}: inte en .npy-fil", sokvag.display())));
    }
    let (header_len, start) = match bytes[6] {
        1 => (u16::from_le_bytes([bytes[8], bytes[9]]) as usize, 10usize),
        2 | 3 => (
            u32::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]) as usize,
            12usize,
        ),
        v => return Err(fel(format!("{}: npy-version {v} stöds inte", sokvag.display()))),
    };
    let header = std::str::from_utf8(&bytes[start..start + header_len])?;
    if header.contains("'fortran_order': True") {
        return Err(fel(format!("{}: Fortran-ordning stöds inte", sokvag.display())));
    }
    let descr = header
        .split("'descr':")
        .nth(1)
        .and_then(|s| s.split('\'').nth(1))
        .ok_or_else(|| fel(format!("{}: descr saknas", sokvag.display())))?
        .to_string();
    let form = header
        .split("'shape':")
        .nth(1)
        .and_then(|s| s.split('(').nth(1))
        .and_then(|s| s.split(')').next())
        .ok_or_else(|| fel(format!("{}: shape saknas", sokvag.display())))?;
    let shape: Vec<usize> = form
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::parse)
        .collect::<Result<_, _>>()?;
    let data = bytes[start + header_len..].to_vec();
    let elem = match descr.as_str() {
        "<f8" => 8,
        "<f4" => 4,
        "|u1" | "<u1" => 1,
        d => return Err(fel(format!("{}: dtype {d} stöds inte", sokvag.display()))),
    };
    let antal: usize = shape.iter().product();
    if data.len() != antal * elem {
        return Err(fel(format!(
            "{}: fel storlek ({} byte, väntade {})",
            sokvag.display(),
            data.len(),
            antal * elem
        )));
    }
    Ok(Npy { descr, shape, data })
}

impl Npy {
    pub fn f64(&self) -> Result<Vec<f64>, Box<dyn std::error::Error>> {
        if self.descr != "<f8" {
            return Err(fel(format!("väntade <f8, fick {}", self.descr)));
        }
        Ok(self
            .data
            .chunks_exact(8)
            .map(|b| f64::from_le_bytes(b.try_into().expect("8 byte")))
            .collect())
    }

    pub fn u8(&self) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
        if self.descr != "|u1" && self.descr != "<u1" {
            return Err(fel(format!("väntade |u1, fick {}", self.descr)));
        }
        Ok(self.data.clone())
    }
}

fn skriv_header(fil: &mut fs::File, descr: &str, shape: &[usize]) -> std::io::Result<()> {
    let form = match shape.len() {
        1 => format!("({},)", shape[0]),
        _ => format!(
            "({})",
            shape.iter().map(|s| s.to_string()).collect::<Vec<_>>().join(", ")
        ),
    };
    let mut header = format!("{{'descr': '{descr}', 'fortran_order': False, 'shape': {form}, }}");
    let total = 10 + header.len() + 1;
    let pad = (64 - total % 64) % 64;
    header.push_str(&" ".repeat(pad));
    header.push('\n');
    fil.write_all(b"\x93NUMPY\x01\x00")?;
    fil.write_all(&(header.len() as u16).to_le_bytes())?;
    fil.write_all(header.as_bytes())
}

pub fn skriv_f64(sokvag: &Path, shape: &[usize], data: &[f64]) -> std::io::Result<()> {
    let mut fil = fs::File::create(sokvag)?;
    skriv_header(&mut fil, "<f8", shape)?;
    let mut buf = Vec::with_capacity(data.len() * 8);
    for v in data {
        buf.extend_from_slice(&v.to_le_bytes());
    }
    fil.write_all(&buf)
}

pub fn skriv_f32(sokvag: &Path, shape: &[usize], data: &[f32]) -> std::io::Result<()> {
    let mut fil = fs::File::create(sokvag)?;
    skriv_header(&mut fil, "<f4", shape)?;
    let mut buf = Vec::with_capacity(data.len() * 4);
    for v in data {
        buf.extend_from_slice(&v.to_le_bytes());
    }
    fil.write_all(&buf)
}
