// Follow the launch.php -> Shibboleth redirect chain with a cookie jar and dump the login form.
const jar = new Map();
function cookieHeader() {
  return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join('; ');
}
function absorb(res) {
  const raw = res.headers.getSetCookie?.() ?? [];
  for (const c of raw) {
    const [pair] = c.split(';');
    const i = pair.indexOf('=');
    if (i > 0) jar.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
  return raw.length;
}

const scheme = 'moodlemobile';
const passport = Math.random().toString(36).slice(2) + Date.now().toString(36);
let url = `https://eclass.yorku.ca/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=${passport}&urlscheme=${scheme}`;

for (let hop = 0; hop < 12 && url; hop++) {
  const res = await fetch(url, {
    redirect: 'manual',
    headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36', Cookie: cookieHeader() },
  });
  const n = absorb(res);
  const text = await res.text();
  console.log(`\n[hop ${hop}] ${res.status} ${url.slice(0, 120)}`);
  console.log(`  cookies+${n} jar=[${[...jar.keys()].join(', ')}] ct=${res.headers.get('content-type')}`);
  const loc = res.headers.get('location');
  if (loc) {
    url = new URL(loc, url).toString();
    console.log('  ->', decodeURIComponent(url).slice(0, 200));
    continue;
  }
  // terminal page: dump form structure
  console.log('  TITLE:', (text.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [, ''])[1].trim().slice(0, 120));
  const forms = [...text.matchAll(/<form\b[^>]*>/gi)].map(m => m[0].replace(/\s+/g, ' '));
  console.log('  FORMS:', forms.length ? forms : '(none)');
  const inputs = [...text.matchAll(/<input\b[^>]*>/gi)].map(m => m[0].replace(/\s+/g, ' '));
  console.log('  INPUTS:', inputs.length ? inputs : '(none)');
  const sel = [...text.matchAll(/<select\b[^>]*>/gi)].map(m => m[0].replace(/\s+/g, ' '));
  console.log('  SELECTS:', sel.length ? sel : '(none)');
  const iframes = [...text.matchAll(/<iframe\b[^>]*>/gi)].map(m => m[0].replace(/\s+/g, ' '));
  if (iframes.length) console.log('  IFRAMES:', iframes);
  console.log('  BODY(1200):', text.replace(/<script[\s\S]*?<\/script>/gi, '').replace(/<style[\s\S]*?<\/style>/gi, '').replace(/\s+/g, ' ').slice(0, 1200));
  break;
}
