# Showcase site — deployment handoff

A complete, self-contained landing page for the stream-clipping automation.
No build step, no framework, no dependencies. Plain HTML/CSS/JS.

```
website/
├── index.html            the entire page (CSS + JS inline)
├── assets/
│   ├── demo.mp4          sample clip, 868 KB
│   ├── demo-poster.jpg   video poster frame
│   ├── kick_logo.png
│   └── twitch_logo.png
└── DEPLOY.md             this file
```

---

## ⚠️ Hard constraint

**Do not deploy this to the Contabo VPS (5.189.185.132).** That box runs a
production WhatsApp system and the clip automation. It must not serve public
web traffic, and it must never be rebooted. Use a static host — options below.

---

## Required edits before going live

Everything needing a decision is marked `EDIT ME` in `index.html`.

### 1. Product name
Currently **ClipEngine** (placeholder). Appears in 5 places:
`<title>`, the two `og:` meta tags, the nav `.brand`, and the footer.
Find-and-replace `ClipEngine` if you want something else.

### 2. Form endpoint — the page is inert without this
The waitlist form posts to the literal string `FORM_ENDPOINT`, which does
nothing. Pick a backend and replace it:

| Option | Setup | Notes |
|---|---|---|
| **Formspree** | formspree.io → new form → copy URL | Free to 50/mo, works with the included JS as-is |
| **Tally / Typeform** | Replace the `<form>` with their embed | More features, heavier |
| **Netlify Forms** | Add `netlify` attribute to `<form>` | Only if hosting on Netlify |

```html
<form class="signup" id="signupForm" action="https://formspree.io/f/XXXXXXX" method="POST">
```

The inline JS submits via `fetch` so the visitor stays on the page and sees a
confirmation. If your provider requires a redirect instead, delete the
`<script>` block at the bottom of `index.html`.

### 3. Privacy / Terms links
The footer points at `/privacy.html` and `/terms.html`, which don't exist in
this folder. Two choices:
- Copy them from the existing GitHub Pages site (they're already written), or
- Point the links at the live URLs:
  `https://bardboy2.github.io/kick-automation/privacy.html` and `/terms.html`

**A privacy policy is not optional here** — the waitlist collects email
addresses, and both TikTok and YouTube ask for a privacy URL during app review.

### 4. Social preview image (optional)
Uncomment the `og:image` meta and drop a 1200×630 PNG at `assets/og.png`.
Without it, shared links render as a bare text card.

### 5. Demo content
`assets/demo.mp4` is a real 10-second clip the system produced, watermarked.
**Confirm this footage is something you're happy to publish publicly** before
going live — swap in a different clip if not. Keep the filename or update the
`<video src>`.

The stats in the demo bar (`8.4 / 10`, `02:14:08`, `45s`) are illustrative.
Either replace them with the real values for whichever clip you feature, or
drop that line.

---

## Deploying

### Cloudflare Pages — recommended
Free, fast, HTTPS automatic, trivial custom domains.

1. Push this `website/` folder to a repo (or use Direct Upload)
2. Cloudflare dashboard → Workers & Pages → Create → Pages
3. Build command: **none**. Output directory: **`/`**
4. Custom domains → add the domain → Cloudflare handles the certificate

### GitHub Pages — also free
1. Push to a repo
2. Settings → Pages → deploy from branch, folder `/` or `/docs`
3. Settings → Pages → Custom domain → enter the domain
4. Create a `CNAME` file containing just the domain
5. At the registrar: `CNAME` record → `USERNAME.github.io`
   (or four `A` records to GitHub's IPs for an apex domain)

### Netlify
Drag the folder onto app.netlify.com. Add the domain under Domain settings.

### Existing host
It's static files — upload `index.html` and `assets/` to the web root over
FTP/SFTP. Keep the relative paths intact.

---

## DNS

| Record | For | Value |
|---|---|---|
| `CNAME` | `www` or subdomain | your host's target |
| `A` / `ALIAS` | apex (`example.com`) | host's IPs — Cloudflare and Netlify support ALIAS at apex; GitHub needs four A records |

Propagation is usually minutes, occasionally up to 48h. **Verify HTTPS is
active before sharing the link** — every host above issues certificates
automatically, but it can take a few minutes after the DNS resolves.

---

## Checks before sharing

- [ ] Video plays on mobile (it's `playsinline`, should be fine on iOS)
- [ ] Waitlist form actually delivers — submit a real test address
- [ ] Privacy and Terms links resolve
- [ ] HTTPS padlock present
- [ ] Open on a phone — layout is responsive, but confirm on a real device
- [ ] Share the link into a chat to check the preview card

---

## Notes on the content

The copy is deliberately specific rather than generic — it describes what the
system genuinely does (audio peak detection, Claude frame scoring at 60/40
weighting, three render formats, Telegram approval, 48-hour cleanup). If you
change the product's behaviour, the page will need updating to match.

The one claim worth keeping honest is **"Running live on Kick & Twitch"** in
the hero badge. It's true today. Don't leave it up if that stops being the case.
