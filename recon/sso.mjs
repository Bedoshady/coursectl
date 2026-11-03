// Walk the Shibboleth SSO flow: launch.php -> SSO -> LoginHandler -> credential form.
const jar = new Map();
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36';
const cookieHeader = () => [...jar.entries()].map(([k, v]) => `${k}=${v}`).join('; ');
function absorb(res) {
  for (const c of res.headers.getSetCookie?.() ?? []) {
    const [pair] = c.split(';');
    const i = pair.indexOf('=');
    if (i > 0) jar.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
}
function report(tag, res, text) {
  console.log(`\n[${tag}] ${res.status} ${res.headers.get('content-type')}`);
  console.log('  TITLE:', (text.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [, ''])[1].trim().slice(0, 140));
  for (const m of text.matchAll(/<form\b[^>]*>/gi)) console.log('  FORM:', m[0].replace(/\s+/g, ' '));
  for (const m of text.matchAll(/<input\b[^>]*>/gi)) console.log('  INPUT:', m[0].replace(/\s+/g, ' ').slice(0, 220));
  for (const m of text.matchAll(/<select\b[^>]*>/gi)) console.log('  SELECT:', m[0].replace(/\s+/g, ' '));
  for (const m of text.matchAll(/<iframe\b[^>]*>/gi)) console.log('  IFRAME:', m[0].replace(/\s+/g, ' '));
  const errs = [...text.matchAll(/(?:class="[^"]*(?:error|alert)[^"]*"[^>]*>)([\s\S]{0,300}?)</gi)].map(m => m[1].replace(/\s+/g, ' ').trim()).filter(Boolean);
  if (errs.length) console.log('  MSGS:', errs.slice(0, 4));
  const body = text.replace(/<script[\s\S]*?<\/script>/gi, '').replace(/<style[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  console.log('  TEXT(700):', body.slice(0, 700));
}

let url = `https://eclass.yorku.ca/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=recon${Date.now()}&urlscheme=moodlemobile`;
let res, text;
for (let hop = 0; hop < 6; hop++) {
  res = await fetch(url, { redirect: 'manual', headers: { 'User-Agent': UA, Cookie: cookieHeader() } });
  absorb(res);
  text = await res.text();
  const loc = res.headers.get('location');
  if (!loc) break;
  url = new URL(loc, url).toString();
}

// Submit the StorageService interstitial (_eventId_proceed) if present.
const csrf = (text.match(/name="csrf_token"\s+value="([^"]+)"/) || [])[1];
if (csrf) {
  console.log('\n>> submitting LoginHandler form1 with csrf', csrf);
  const form = new URLSearchParams();
  form.set('csrf_token', csrf);
  form.set('shib_idp_ls_success.shib_idp_session_ss', 'false');
  form.set('shib_idp_ls_exception.shib_idp_session_ss', '');
  form.set('shib_idp_ls_value.shib_idp_session_ss', '');
  form.set('shib_idp_ls_success.shib_idp_persistent_ss', 'false');
  form.set('shib_idp_ls_exception.shib_idp_persistent_ss', '');
  form.set('shib_idp_ls_value.shib_idp_persistent_ss', '');
  form.set('shib_idp_ls_supported', '');
  form.set('_eventId_proceed', '');
  res = await fetch(url, {
    method: 'POST',
    redirect: 'manual',
    headers: { 'User-Agent': UA, Cookie: cookieHeader(), 'Content-Type': 'application/x-www-form-urlencoded', Referer: url, Origin: 'https://shib.yorku.ca' },
    body: form.toString(),
  });
  absorb(res);
  text = await res.text();
  const loc2 = res.headers.get('location');
  console.log('  post status', res.status, 'loc:', loc2 ? decodeURIComponent(loc2).slice(0, 200) : '(none)');
  if (loc2) {
    url = new URL(loc2, url).toString();
    for (let h = 0; h < 12; h++) {
      res = await fetch(url, { redirect: 'manual', headers: { 'User-Agent': UA, Cookie: cookieHeader() } });
      absorb(res);
      text = await res.text();
      const next = res.headers.get('location');
      console.log(`  hop ${h}: ${res.status} ${url.slice(0, 130)}${next ? ' -> ' + decodeURIComponent(new URL(next, url).toString()).slice(0, 160) : ''}`);
      if (!next) break;
      url = new URL(next, url).toString();
    }
  }
  report('credential-form', res, text);
}

import { writeFileSync } from 'node:fs';
writeFileSync(new URL('./ssologin.html', import.meta.url), text);
console.log('\n[saved] recon/ssologin.html', text.length, 'bytes');
