//! Parse GRIB2 .idx index files to find byte ranges for variables.
//!
//! IDX format (one line per GRIB message):
//!   msg_num:byte_offset:d=YYYYMMDDHHH:VAR:level:description:
//!
//! Example:
//!   84:59048206:d=2026022800:APCP:surface:0-1 hour acc fcst:

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
        let parts: Vec<&str> = line.splitn(4, ':').collect();
        if parts.len() < 4 {
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

        // Rest after "msg:offset:" is "d=datetime:VAR:level:desc:"
        let remainder = parts[3];
        let rest_parts: Vec<&str> = remainder.split(':').collect();
        if rest_parts.len() < 3 {
            continue;
        }

        let datetime = rest_parts[0].to_string();

        // Build search_this from VAR:level:desc onwards
        let search_parts: Vec<&str> = rest_parts[1..].to_vec();
        let search_this = format!(":{}:", search_parts.join(":").trim_end_matches(':'));

        let variable = rest_parts.get(1).unwrap_or(&"").to_string();
        let level = rest_parts.get(2).unwrap_or(&"").to_string();
        let description = rest_parts[3..].join(":");
        let description = description.trim_end_matches(':').to_string();

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
    let pattern = search.trim_start_matches(':').trim_end_matches(':');
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
}
