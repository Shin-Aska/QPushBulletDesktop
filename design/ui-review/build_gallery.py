"""Build a local image review gallery. Does not modify the application or images."""
import html
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / 'capture-manifest.json').read_text())
# Keep initial generated drafts, but present the newest version of each board.
groups = {}
for path in (ROOT / 'proposed').glob('*.png'):
    groups.setdefault(path.name[:2], []).append(path)
def version(path):
    match = re.search(r'-v(\d+)$', path.stem)
    return (int(match.group(1)) if match else 1, path.stat().st_mtime)
concepts = [max(groups[key], key=version) for key in sorted(groups)]
titles = {
    '01': 'Inbox', '02': 'Devices', '03': 'Outbox', '04': 'Compose and edit',
    '05': 'Review and uncertain outcomes', '06': 'Settings and service controls',
    '07': 'Web status companion', '08': 'Empty and connection states',
    '09': 'Add, remove, and encryption flows',
}


def card(path, title, subtitle, kind):
    relative = path.relative_to(ROOT).as_posix()
    escaped = html.escape(relative, quote=True)
    label = html.escape(title, quote=True)
    return f'''<article class="card {kind}">
      <button class="image-button" data-image="{escaped}" data-title="{label}" aria-label="Enlarge {label}">
        <img src="{escaped}" alt="{label}" loading="lazy">
      </button>
      <div class="caption"><div><h3>{html.escape(title)}</h3><p>{html.escape(subtitle)}</p></div>
        <a href="{escaped}" target="_blank" rel="noopener">Original PNG ↗</a></div>
    </article>'''


proposal_cards = '\n'.join(card(path, titles.get(path.name[:2], path.stem.replace('-', ' ').title()),
                               'Pending-action view · generated mockup' if path.name[:2] == '03'
                               else 'Revised target design · generated mockup' if path.name[:2] in {'01', '02', '09'}
                               else 'Earlier visual proposal · generated mockup', 'concept') for path in concepts)
current_cards = '\n'.join(card(ROOT / screen['file'], screen['title'],
                              'Actual UI capture · fictional data · ' + screen['surface'], 'current')
                          for screen in manifest['screens'])
template = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>pbdesk — Sol visual review</title>
<style>
:root{color-scheme:light;--ink:#172126;--muted:#5f6d75;--line:#dce2e5;--paper:#f4f6f7;--white:#fff}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{background:var(--white);border-bottom:1px solid var(--line)}.wrap{max-width:1500px;margin:auto;padding:32px}
.eyebrow{margin:0 0 10px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}h1{font-size:36px;line-height:1.15;letter-spacing:-.035em;margin:0 0 14px}h2{font-size:25px;letter-spacing:-.02em;margin:0}p{max-width:880px}.intro{color:var(--muted);margin:0 0 20px}.pills{display:flex;gap:8px;flex-wrap:wrap}.pill{border:1px solid var(--line);border-radius:99px;padding:5px 12px;font-size:13px;background:var(--paper)}
nav{display:flex;gap:20px;flex-wrap:wrap;padding-top:22px}a{color:#235864;text-underline-offset:4px}button{font:inherit}.section-heading{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin:12px 0 20px}.section-heading p{color:var(--muted);font-size:14px;margin:0}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.concept:first-child{grid-column:1/-1}.card{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--white);min-width:0}.image-button{width:100%;padding:12px;border:0;background:#e9edef;cursor:zoom-in;display:block}.image-button img{display:block;width:100%;height:360px;object-fit:contain}.concept:first-child img{height:auto;max-height:850px}.caption{padding:17px;display:flex;justify-content:space-between;gap:14px;align-items:baseline}.caption h3{font-size:16px;margin:0 0 4px}.caption p,.caption a{font-size:12px;margin:0;color:var(--muted)}.caption a{white-space:nowrap}
.current-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.current img{height:230px}.section{margin-bottom:54px}.note{border-left:3px solid #768e94;padding:12px 18px;background:var(--white);color:var(--muted);font-size:14px;margin:24px 0}.links{display:flex;gap:20px;flex-wrap:wrap;font-size:14px}
dialog{border:1px solid #88979e;border-radius:10px;max-width:97vw;width:1500px;padding:0;background:#172126;color:white}dialog::backdrop{background:#0c151bd9}.viewer-bar{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:12px 18px;border-bottom:1px solid #40515b}.viewer-bar a{color:#d5e8ed}.viewer-bar button{background:#edf3f5;color:#172126;border:0;border-radius:6px;padding:7px 15px;cursor:pointer}.viewer-img{display:block;max-width:100%;max-height:85vh;width:auto;margin:auto;object-fit:contain}.viewer-actions{display:flex;gap:20px;align-items:center}
@media(max-width:950px){.current-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.caption{flex-direction:column;gap:7px}}
@media(max-width:650px){.wrap{padding:22px 15px}h1{font-size:29px}.grid,.current-grid{grid-template-columns:1fr}.concept:first-child{grid-column:auto}.image-button img{height:auto;max-height:620px}.section-heading{display:block}.viewer-bar{align-items:flex-start}.viewer-actions{gap:10px}.caption{padding:14px}}
</style></head><body>
<header><div class="wrap"><p class="eyebrow">pbdesk · visual exploration</p><h1>Sol’s visual design review</h1>
<p class="intro">A slate-and-jade native desktop direction with warm-white reading surfaces. The revised concepts show device switching, Pushbullet and SMS history, local records before background updates, and device registration, removal, and encryption settings. These images describe the intended product for your feedback. Application code has not been redesigned or changed.</p>
<div class="pills"><span class="pill">{{BOARD_COUNT}} concept boards</span><span class="pill">18 current UI captures</span><span class="pill">Fictional data only</span><span class="pill">Awaiting your finalization</span></div>
<nav><a href="#concepts">Sol’s concepts</a><a href="#current">Current screens</a><a href="accepted-requirements.md">Your design requirements</a><a href="sol-direction.md">Direction and rationale</a><a href="prompts.json">Exact image prompts</a></nav></div></header>
<main class="wrap"><section id="concepts" class="section"><div class="section-heading"><h2>Proposed direction</h2><p>Click any image to inspect it at a larger size.</p></div>
<div class="grid">{{PROPOSALS}}</div><div class="note">Generated concepts communicate visual direction. Fine text and details remain open for refinement. Implemented security and workflow behavior must remain intact: explicit destinations, separate review/approval, truthful sending privacy, and uncertain outcomes without automatic retry.</div></section>
<section id="current" class="section"><div class="section-heading"><h2>Current application</h2><p>Every main view, dialog family, service menu, and status-page layout.</p></div><div class="grid current-grid">{{CURRENT}}</div></section>
<footer class="links"><a href="capture-manifest.json">Capture inventory</a><a href="README.md">Review notes</a></footer></main>
<dialog id="viewer"><div class="viewer-bar"><strong id="viewer-title"></strong><div class="viewer-actions"><a id="viewer-original" target="_blank" rel="noopener">Original PNG ↗</a><button id="close-viewer" type="button">Close · Esc</button></div></div><img id="viewer-image" class="viewer-img" alt=""></dialog>
<script>
const viewer=document.getElementById('viewer');
document.querySelectorAll('[data-image]').forEach(button=>button.addEventListener('click',()=>{
  const img=document.getElementById('viewer-image');img.src=button.dataset.image;img.alt=button.dataset.title;
  document.getElementById('viewer-title').textContent=button.dataset.title;
  document.getElementById('viewer-original').href=button.dataset.image;viewer.showModal();
}));
document.getElementById('close-viewer').addEventListener('click',()=>viewer.close());
viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.close()});
</script></body></html>'''
page = template.replace('{{BOARD_COUNT}}', str(len(concepts))).replace('{{PROPOSALS}}', proposal_cards).replace('{{CURRENT}}', current_cards)
(ROOT / 'index.html').write_text(page)
readme = ['# pbdesk visual review', '', 'Image-only first-pass redesign requested for user finalization. No application source or live services were changed.', '',
          'Open [the visual gallery](index.html) to compare the current application and Sol’s proposed images.', '',
          'Your latest device management, full SMS history, local SQLite, background synchronization, and per-device KWallet requirements are recorded in [the design requirements](accepted-requirements.md). These describe the target product, not features already added by this visual pass.', '',
          '## Sol’s concepts', '']
for path in concepts:
    readme.append(f'- [{titles.get(path.name[:2], path.stem)}]({path.relative_to(ROOT).as_posix()})')
readme += ['', 'The creative direction and exact prompt set are recorded in [Sol’s notes](sol-direction.md) and [prompts.json](prompts.json). Built-in image generation was used; no CLI/API fallback or dependency installation was used.', '',
           '## Current captures', '', 'All 18 captures use fictional fixtures. Native windows were captured from the actual Qt Widgets on Wayland; web views use the existing Firefox installation with external requests blocked. The capture worker never contacts the live core or reads credentials.', '']
for screen in manifest['screens']:
    readme.append(f"- [{screen['title']}]({screen['file']})")
readme += ['', 'The current screenshots are evidence of existing UI. The proposed images are design mockups, not screenshots of implemented changes. Next is the user’s feedback and visual finalization before any UI implementation.', '']
(ROOT / 'README.md').write_text('\n'.join(readme))
print(json.dumps({'concept_boards': len(concepts), 'current_captures': len(manifest['screens']), 'gallery': str(ROOT / 'index.html')}))
