"""Local static styles. No remote fonts, assets or scripts."""

APP_STYLES = """<style>
:root {--co-ink:#1c2c45;--co-muted:#63718a;--co-line:#dfe5ee;}
.stApp {background:#f5f7fa;color:var(--co-ink);}
.block-container {max-width:1240px;padding:1.5rem 2.5rem 3rem;}
.block-container > [data-testid="stVerticalBlock"] {gap:.8rem;}
h1 {font-size:clamp(1.6rem,2.5vw,2rem)!important;letter-spacing:-.025em;padding:.2rem 0!important;}
h2 {font-size:1.45rem!important;} h3 {font-size:1.18rem!important;letter-spacing:.01em;}
[data-testid="stCaptionContainer"] {color:var(--co-muted);line-height:1.65;}
[data-testid="stMetric"] {background:white;border:1px solid var(--co-line);border-radius:12px;padding:.7rem 1rem;}
[data-testid="stMetricValue"] {font-size:1.7rem;font-variant-numeric:tabular-nums;}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width:0;}
[data-testid="stRadio"] [role="radiogroup"] {background:#e9eef5;border-radius:12px;padding:.55rem .8rem;gap:.6rem 1.5rem;flex-wrap:wrap;}
[data-testid="stForm"] {background:white;border:1px solid var(--co-line);border-radius:14px;padding:1.2rem;}
[data-testid="stForm"] [data-testid="stVerticalBlock"] {gap:.7rem;}
[data-testid="stFormSubmitButton"] button,[data-testid="stButton"] button {
 min-height:2.65rem;border-radius:9px;font-weight:550;white-space:normal;line-height:1.45;box-shadow:none;}
[data-testid="stExpander"] {margin-top:.4rem;background:rgba(255,255,255,.75);border-radius:12px;}
[data-testid="stAlert"] {border-radius:10px;line-height:1.65;}
[role="tablist"] {gap:1.5rem;border-bottom:1px solid var(--co-line);margin:.25rem 0 1rem;}
[role="tab"] {font-size:.98rem;padding:.6rem .15rem;}
.campusops-steps {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.75rem;}
.campusops-step {border:1px solid var(--co-line);background:white;border-radius:10px;padding:.65rem .85rem;line-height:1.55;color:var(--co-muted);}
.campusops-step.is-current {border-color:#7392b5;background:#eaf0f8;color:#244d7c;font-weight:600;}
.campusops-step small {display:block;font-weight:400;font-size:.82rem;}
.campusops-summary {display:flex;flex-wrap:wrap;gap:.55rem 1.3rem;margin:.5rem 0 1rem;line-height:1.6;}
.campusops-summary strong {font-size:1.1rem;}
.campusops-cards {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.85rem;}
.campusops-card {background:white;border:1px solid var(--co-line);border-radius:12px;padding:1rem 1.1rem;min-width:0;overflow-wrap:anywhere;}
.campusops-card h4 {color:var(--co-ink);margin:0 0 .45rem;font-size:1rem;line-height:1.5;font-weight:600;}
.campusops-card p {margin:.3rem 0;line-height:1.65;font-size:.92rem;}
.campusops-card .muted {color:var(--co-muted);}
.campusops-record-list {padding:.1rem 0;}
.campusops-record-list .campusops-card {margin-bottom:.75rem;}
.campusops-change {display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);gap:1rem;align-items:center;background:#f6f8fb;border-radius:8px;padding:.6rem .8rem;margin:.6rem 0;}
.campusops-change strong {font-size:1.05rem;overflow-wrap:anywhere;}
.campusops-badge {display:inline-block;border-radius:5px;padding:.12rem .5rem;font-size:.8rem;font-weight:550;}
.status-available {background:#e8f3ee;color:#2e684d;}
.status-faulted {background:#f9e9e8;color:#a43e3a;}
.status-maintenance {background:#f6efdc;color:#87672d;}
.campusops-records {border:1px solid var(--co-line);border-radius:10px;background:white;}
.campusops-records td,.campusops-records th {border:0!important;border-bottom:1px solid var(--co-line)!important;padding:.65rem .8rem!important;}
@media(max-width:900px) {
 .block-container {padding:1rem 1.1rem 2rem;}
 .campusops-cards {grid-template-columns:repeat(2,minmax(0,1fr));}
 [data-testid="stForm"] {padding:.9rem;}
 [data-testid="stForm"] [data-testid="stHorizontalBlock"] {flex-wrap:wrap;}
 [data-testid="stForm"] [data-testid="stColumn"] {flex:1 1 100%!important;width:100%!important;}
}
@media(max-width:520px) {
 .campusops-cards {grid-template-columns:1fr;}
 .campusops-steps {gap:.35rem;}
 .campusops-step {padding:.5rem;font-size:.85rem;}
 .campusops-step small {font-size:.72rem;}
 .campusops-change {gap:.4rem;padding:.5rem;}
}
</style>"""
