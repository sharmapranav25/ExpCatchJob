#!/bin/zsh
# Handles jobagent://file/<base64url path> links from the status page (via the Job Agent app).
# WebKit refuses to navigate a file:// page to another file:// path outside its own folder
# (the crash-recovery "This webpage was reloaded" banner), so the status page can't link straight
# to a PDF or report. This hands the real path to `open` instead, which lets macOS's own default
# app (Preview, a markdown viewer, ...) show it -- no browser file:// restriction involved.
#
# Only a path that resolves inside this repo's output/ or reports/ folder is ever opened, so a
# malicious page elsewhere can't use this scheme to open an arbitrary file on the Mac.
JOBS="${JOB_AGENT_REPO:-/absolute/path/to/your/career-ops/checkout}"
log="$HOME/.job-agent/open.log"
url="$1"
b64="${url#jobagent://file/}"
# URL-safe base64 (-_ in place of +/, no padding) -> standard base64 for `base64 -D`.
std="$(print -r -- "$b64" | /usr/bin/tr '_-' '/+')"
case $(( ${#std} % 4 )) in 2) std="${std}==";; 3) std="${std}=";; esac
path="$(print -r -- "$std" | /usr/bin/base64 -D 2>/dev/null)"
now="$(/bin/date '+%F %T')"
# Pure-zsh dirname/basename (no external tools, since `do shell script` gives a minimal PATH):
# resolve the directory with :A (absolute, symlinks resolved) and rejoin the filename.
dir="${path:h}"; file="${path:t}"
resolved="${dir:A}/$file"
if [[ -z "$path" || ( "$resolved" != "$JOBS/output/"* && "$resolved" != "$JOBS/reports/"* ) || ! -f "$resolved" ]]; then
  print "$now refused: $url -> '$path'" >> "$log"
  exit 1
fi
print "$now opened: $resolved" >> "$log"
/usr/bin/open "$resolved"
