//! Parse GRIB2 index files to find byte ranges for variables.
//!
//! Supports two formats:
//!
//! **NOAA `.idx`** (one line per GRIB message):
//!   msg_num:byte_offset:d=YYYYMMDDHHH:VAR:level:description:
//!   Example: 84:59048206:d=2026022800:APCP:surface:0-1 hour acc fcst:
//!
//! **ECMWF `.index`** (JSON-lines, one object per GRIB message):
//!   {"param":"2t","_offset":71950353,"_length":665509,...}

use regex::Regex;
use std::path::Path;

/// A single entry from a .idx file
#[derive(Debug, Clone)]
pub struct IdxEntry {
    pub message_num: u32,
    pub byte_offset: u64,
    pub datetime: String,
    pub variable: String,
    pub level: String,
    pub description: String,
    /// Full colon-delimited search string (e.g., ":APCP:surface:0-1 hour acc fcst:")
    pub search_this: String,
}

/// Parsed result with byte range resolved
#[derive(Debug, Clone)]
pub struct IdxMatch {
    pub entry: IdxEntry,
    pub byte_start: u64,
    /// None if this is the last message (download to EOF)
    pub byte_end: Option<u64>,
}

/// Parse an idx file from text content into entries
pub fn parse_idx(content: &str) -> Vec<IdxEntry> {
    let mut entries = Vec::new();

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        // Format: msg_num:byte_offset:d=datetime:VAR:level:description:
        let parts: Vec<&str> = line.split(':').collect();
        // Minimum: msg, offset, d=datetime, VAR, level, desc (6 parts)
        if parts.len() < 6 {
            continue;
        }

        let message_num = match parts[0].parse::<u32>() {
            Ok(n) => n,
            Err(_) => continue,
        };

        let byte_offset = match parts[1].parse::<u64>() {
            Ok(n) => n,
            Err(_) => continue,
        };

        let datetime = parts[2].to_string(); // "d=YYYYMMDDHHH"
        let variable = parts[3].to_string();
        let level = parts[4].to_string();
        let description = parts[5..].join(":");
        let description = description.trim_end_matches(':').to_string();

        // Build search_this: ":VAR:level:desc:"
        let search_tail = parts[3..].join(":");
        let search_this = format!(":{}:", search_tail.trim_end_matches(':'));

        entries.push(IdxEntry {
            message_num,
            byte_offset,
            datetime,
            variable,
            level,
            description,
            search_this,
        });
    }

    entries
}

/// Find entries matching a search pattern (supports regex)
///
/// The search pattern is matched against each entry's search_this field.
/// Common patterns: ":APCP:surface", ":TMP:2 m above ground"
pub fn find_matches(entries: &[IdxEntry], search: &str) -> Vec<IdxMatch> {
    let pattern = search.trim_end_matches(':');
    let re = match Regex::new(pattern) {
        Ok(r) => r,
        Err(_) => return Vec::new(),
    };

    let mut matches = Vec::new();

    for (i, entry) in entries.iter().enumerate() {
        if re.is_match(&entry.search_this) {
            let byte_end = entries.get(i + 1).map(|next| next.byte_offset.saturating_sub(1));

            matches.push(IdxMatch {
                entry: entry.clone(),
                byte_start: entry.byte_offset,
                byte_end,
            });
        }
    }

    matches
}

/// Convenience: find the first match for a search pattern
pub fn find_first(entries: &[IdxEntry], search: &str) -> Option<IdxMatch> {
    find_matches(entries, search).into_iter().next()
}

/// Parse an idx file from a file path
pub fn parse_idx_file(path: &Path) -> anyhow::Result<Vec<IdxEntry>> {
    let content = std::fs::read_to_string(path)?;
    Ok(parse_idx(&content))
}

// ── ECMWF JSON-lines index format ───────────────────────────────────────────

/// A single entry from an ECMWF `.index` (JSON-lines) file
#[derive(Debug, Clone, serde::Deserialize)]
struct EcmwfIndexEntry {
    param: String,
    #[serde(rename = "_offset")]
    offset: u64,
    #[serde(rename = "_length")]
    length: u64,
    #[serde(default)]
    levtype: String,
    #[serde(default)]
    step: String,
    #[serde(default)]
    levelist: Option<String>,
}

/// Parse ECMWF JSON-lines index format into IdxEntry vec.
///
/// Maps each JSON line to an IdxEntry with:
///   - search_this built as `:param:levtype:` for regex matching
///   - byte_offset from `_offset`, byte length from `_length`
pub fn parse_ecmwf_index(content: &str) -> Vec<IdxEntry> {
    let mut entries = Vec::new();
    for (i, line) in content.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let entry: EcmwfIndexEntry = match serde_json::from_str(line) {
            Ok(e) => e,
            Err(_) => continue,
        };

        // Build a search_this string similar to NOAA idx for regex matching.
        // Format: ":param:levtype:" so ":2t:" or ":tp:" matches.
        let search_this = format!(":{}:{}:", entry.param, entry.levtype);

        entries.push(IdxEntry {
            message_num: (i + 1) as u32,
            byte_offset: entry.offset,
            datetime: String::new(),
            variable: entry.param.clone(),
            level: if let Some(ref lev) = entry.levelist {
                format!("{} {}", lev, entry.levtype)
            } else {
                entry.levtype.clone()
            },
            description: format!("step {}", entry.step),
            search_this,
        });
    }
    entries
}

/// Detect format and parse: if content starts with '{', use ECMWF JSON-lines; otherwise NOAA idx.
pub fn parse_index_auto(content: &str) -> Vec<IdxEntry> {
    let first_non_empty = content.trim_start();
    if first_non_empty.starts_with('{') {
        parse_ecmwf_index(content)
    } else {
        parse_idx(content)
    }
}

/// For ECMWF entries, byte_end = offset + length - 1 (we know exact size).
/// Override the standard find_matches which uses next entry's offset.
pub fn find_matches_with_length(entries: &[IdxEntry], search: &str, lengths: &[u64]) -> Vec<IdxMatch> {
    let pattern = search.trim_end_matches(':');
    let re = match Regex::new(pattern) {
        Ok(r) => r,
        Err(_) => return Vec::new(),
    };

    let mut matches = Vec::new();
    for (i, entry) in entries.iter().enumerate() {
        if re.is_match(&entry.search_this) {
            let byte_end = if i < lengths.len() && lengths[i] > 0 {
                Some(entry.byte_offset + lengths[i] - 1)
            } else {
                entries.get(i + 1).map(|next| next.byte_offset.saturating_sub(1))
            };
            matches.push(IdxMatch {
                entry: entry.clone(),
                byte_start: entry.byte_offset,
                byte_end,
            });
        }
    }
    matches
}

/// Parse ECMWF index and return entries + lengths (for precise byte ranges).
pub fn parse_ecmwf_index_with_lengths(content: &str) -> (Vec<IdxEntry>, Vec<u64>) {
    let mut entries = Vec::new();
    let mut lengths = Vec::new();
    for (i, line) in content.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let entry: EcmwfIndexEntry = match serde_json::from_str(line) {
            Ok(e) => e,
            Err(_) => continue,
        };

        let search_this = format!(":{}:{}:", entry.param, entry.levtype);

        entries.push(IdxEntry {
            message_num: (i + 1) as u32,
            byte_offset: entry.offset,
            datetime: String::new(),
            variable: entry.param.clone(),
            level: if let Some(ref lev) = entry.levelist {
                format!("{} {}", lev, entry.levtype)
            } else {
                entry.levtype.clone()
            },
            description: format!("step {}", entry.step),
            search_this,
        });
        lengths.push(entry.length);
    }
    (entries, lengths)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE_IDX: &str = "\
1:0:d=2026022800:REFC:entire atmosphere:1 hour fcst:
2:209383:d=2026022800:RETOP:cloud top:1 hour fcst:
3:322463:d=2026022800:var discipline=0 center=7 local_table=1 parmcat=16 parm=201:entire atmosphere:1 hour fcst:
84:59048206:d=2026022800:APCP:surface:0-1 hour acc fcst:
85:59238420:d=2026022800:NCPCP:surface:0-1 hour acc fcst:
";

    #[test]
    fn test_parse_idx() {
        let entries = parse_idx(SAMPLE_IDX);
        assert_eq!(entries.len(), 5);
        assert_eq!(entries[0].message_num, 1);
        assert_eq!(entries[0].byte_offset, 0);
        assert_eq!(entries[0].variable, "REFC");
        assert_eq!(entries[3].message_num, 84);
        assert_eq!(entries[3].byte_offset, 59048206);
        assert_eq!(entries[3].variable, "APCP");
    }

    #[test]
    fn test_find_apcp() {
        let entries = parse_idx(SAMPLE_IDX);
        let m = find_first(&entries, ":APCP:surface").unwrap();
        assert_eq!(m.byte_start, 59048206);
        assert_eq!(m.byte_end, Some(59238419));
    }

    #[test]
    fn test_search_regex() {
        let entries = parse_idx(SAMPLE_IDX);
        let matches = find_matches(&entries, "APCP:surface:.*acc fcst");
        assert_eq!(matches.len(), 1);
    }

    #[test]
    fn test_last_entry_no_end() {
        let entries = parse_idx(SAMPLE_IDX);
        let m = find_first(&entries, ":NCPCP:surface").unwrap();
        assert_eq!(m.byte_start, 59238420);
        assert_eq!(m.byte_end, None); // last entry
    }

    const SAMPLE_ECMWF_INDEX: &str = r#"{"domain":"g","date":"20260227","time":"1200","expver":"0001","class":"od","type":"fc","stream":"oper","step":"3","levtype":"sfc","param":"tp","_offset":25509266,"_length":591695}
{"domain":"g","date":"20260227","time":"1200","expver":"0001","class":"od","type":"fc","stream":"oper","step":"3","levtype":"sfc","param":"sd","_offset":26856447,"_length":186925}
{"domain":"g","date":"20260227","time":"1200","expver":"0001","class":"od","type":"fc","stream":"oper","step":"3","levtype":"sfc","param":"2t","_offset":71950353,"_length":665509}
"#;

    #[test]
    fn test_parse_ecmwf_index() {
        let entries = parse_ecmwf_index(SAMPLE_ECMWF_INDEX);
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[0].variable, "tp");
        assert_eq!(entries[0].byte_offset, 25509266);
        assert_eq!(entries[1].variable, "sd");
        assert_eq!(entries[2].variable, "2t");
        assert_eq!(entries[2].byte_offset, 71950353);
    }

    #[test]
    fn test_find_ecmwf_2t() {
        let (entries, lengths) = parse_ecmwf_index_with_lengths(SAMPLE_ECMWF_INDEX);
        let matches = find_matches_with_length(&entries, ":2t:", &lengths);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].byte_start, 71950353);
        assert_eq!(matches[0].byte_end, Some(71950353 + 665509 - 1));
    }

    #[test]
    fn test_auto_detect_ecmwf() {
        let entries = parse_index_auto(SAMPLE_ECMWF_INDEX);
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[0].variable, "tp");
    }

    #[test]
    fn test_auto_detect_noaa() {
        let entries = parse_index_auto(SAMPLE_IDX);
        assert_eq!(entries.len(), 5);
        assert_eq!(entries[0].variable, "REFC");
    }
}
