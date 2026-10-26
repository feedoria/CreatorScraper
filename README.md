# CreatorScout

Găsește și clasează YouTuberii potriviți pentru promovarea unui joc indie, după cât de aproape le e conținutul de joc. Folosește API-ul oficial YouTube Data v3, fără scraping și fără Selenium.

## Pornire rapidă (Mac)

1. Dublu-click pe **`Build app (Mac).command`**. Terminalul se deschide **o singură dată**, timp de 1–3 minute, cât se construiește aplicația. La final se deschide singură **CreatorScout.app**, pe care o găsești de acum în `~/Applications` (o poți trage și în Dock).
   - Dacă macOS spune că fișierul nu poate fi deschis: click dreapta → Open → Open.
2. De acum încolo deschizi doar **CreatorScout.app**, ca pe orice aplicație.

## Pornire rapidă (Windows)

1. **Obține .exe-ul**: dublu-click pe `build_exe.bat`. Prima dată durează 1–3 minute. La final apare `CreatorScout.exe` în același folder, iar de atunci încolo rulezi doar exe-ul.
   - Ai nevoie de Python 3.10+ instalat ([python.org](https://www.python.org/downloads/)), cu „Add python.exe to PATH” bifat. Doar pentru build: exe-ul final rulează și pe calculatoare fără Python.
   - Alternativ, rulezi direct din cod cu `run_from_source.bat`.
2. **Cheia API** (gratuită): tab-ul **Setări** din aplicație are pașii. Pe scurt: Google Cloud Console → proiect nou → activezi *YouTube Data API v3* → Credentials → API key.
3. **Tab-ul 1. Jocul**: completezi descrierea jocului (în engleză, ca pe Steam), 5–15 jocuri comparabile și, opțional, câteva cuvinte-cheie. Apoi **Caută și clasează**.
4. **Tab-ul 2. Rezultate**: lista ordonată, cu top N evidențiat. Click pe un rând arată de ce are scorul respectiv, emailurile publice, linkurile și ultimele video-uri. De acolo poți copia emailul sau un draft personalizat, marca un creator ca „contactat” și exporta CSV.

## Cum se calculează scorul (0–100)

| Componentă | Pondere | Ce măsoară |
|---|---|---|
| Conținut | 35% | Similaritate TF-IDF între descrierea jocului și titlul, descrierea și titlurile recente ale canalului |
| Jocuri similare | 30% | Câte jocuri comparabile a jucat (1 → 0.5, 2 → 0.75, 3 → 0.875…) |
| Mărime | 15% | 3k–100k abonați = ideal pentru indie; scade spre 100 și spre 5M |
| Engagement | 10% | Views median pe ultimele ~15 video-uri raportat la abonați |
| Activitate | 10% | A postat în ultimele 14 zile = maxim; scade până la 120 de zile |

Pe lângă asta, **canalele off-topic sunt penalizate** (până la ×0.35): un canal mare și activ care nu are legătură cu jocul nu ajunge sus.

Filtre dure: intervalul de abonați, inactivitatea maximă și (opțional) excluderea celor deja contactați.

## Cota API

10.000 de unități pe zi, gratuit. O căutare costă 100 de unități, analiza unui canal aproximativ 2. Aplicația îți arată costul estimat înainte de rulare. Tot ce descarcă intră în cache local (3–7 zile), așa că dacă repeți căutarea sau schimbi doar filtrele nu mai consumi nimic. Dacă cota se termină la jumătate, primești totuși clasamentul cu ce a apucat să adune.

Ca să găsești **mai mulți** creatori: adaugă mai multe jocuri comparabile (fiecare aduce alți oameni) sau crește „Pagini / căutare” (fiecare pagină = încă 50 de video-uri, 100 de unități).

## Ce date de contact găsește (și ce nu)

- **Da**: emailurile pe care creatorul le-a scris public în descrierea canalului sau a video-urilor (inclusiv forme ca `nume [at] gmail [dot] com`), plus linkuri către Twitter/X, Discord, Linktree, Twitch etc.
- **Nu**: emailul din butonul „View email address” din About. YouTube îl ascunde intenționat în spatele unui login + CAPTCHA, iar ocolirea lui încalcă termenii YouTube. Pentru canalele din top fără email, îl vezi manual în 10 secunde, sau scrii pe Twitter/Discord.

## Recomandări de folosire

- **Trimite manual, personalizat, puțini oameni.** 30–50 de mailuri bine țintite bat 1.000 de mailuri generice. Mass-mailingul ajunge în spam și strică reputația domeniului. Draft-ul generat e doar un punct de plecare: adaugă o frază despre un video anume al creatorului.
- Datele personale (emailuri) sunt sub GDPR: le folosești doar pentru outreach, nu le vinzi și nu le publici, iar dacă cineva cere să nu mai fie contactat, îl marchezi și îl lași în pace.
- Folosește în paralel Keymailer / Woovit / Lurkit pentru distribuirea de key-uri.

## Structura codului

- `app.py`: interfața (Tkinter, inclus în Python, deci exe-ul iese mic)
- `scout.py`: pipeline-ul (discovery → îmbogățire → scoring → explicații), separat de UI și testabil
- `youtube_api.py`: clientul API, cu cache, retry și contorizarea cotei
- `contacts.py`: extragerea emailurilor și linkurilor publice
- `storage.py`: SQLite (cache, istoric contactați) și setări JSON în `%APPDATA%\CreatorScout`
- `tests/test_pipeline.py`: test cap-coadă cu un API YouTube simulat (`python tests/test_pipeline.py`)
- `.github/workflows/build-exe.yml`: dacă urci proiectul pe GitHub, exe-ul se construiește automat (tab-ul Actions → artefactul *CreatorScout-exe*)

## Pivotul spre joburi (mai târziu)

`scout.py` e deja generic ca idee: „ordonează entități după cât de aproape le e textul de un profil”. Pentru joburi înlocuiești `youtube_api.py` cu o sursă de anunțuri, descrierea jocului cu CV-ul și „jocurile comparabile” cu skill-urile cheie.

## Găsirea emailurilor pentru creatorii fără email public

Aplicația are trei niveluri, în ordine:

1. **Descrierile YouTube** (automat): emailurile scrise în descrierea canalului și a video-urilor.
2. **Linkurile creatorului** (automat, `enrich.py`): pentru creatorii din top N fără email, aplicația vizitează Linktree / Beacons / Carrd și site-ul personal (plus pagina Contact/About), cu `requests`. Respectă `robots.txt`, vizitează maximum câteva pagini per creator și ține rezultatele în cache 7 zile. Nu atinge X/Twitter, Instagram sau TikTok.
   - **Browser fallback (Selenium):** pentru paginile construite din JavaScript, deschide un Edge (sau Chrome) ascuns și citește pagina după ce s-a încărcat. La prima folosire, Selenium descarcă automat driver-ul potrivit, deci e nevoie de internet.
3. **Reveal email** (manual): butonul deschide pagina About a canalului. Dai click pe „View email address”, rezolvi CAPTCHA-ul, lipești emailul în aplicație și rămâne salvat pentru rulările următoare.

În tabel și în CSV, coloana **FOUND ON / email_found_on** arată de unde vine emailul: `YouTube`, `Linktree`, `website`, `browser` sau `manual`.

## Predicția (cum sunt clasați creatorii)

Fiecare creator primește trei cifre:

- **Coverage chance:** cât de probabil e ca un canal ca ăsta să acopere un joc ca al tău.
- **Predicted views:** câte views ar avea probabil un video despre joc. Se calculează ca mediana canalului × cât de bine îi merg video-urile cu jocuri similare.
- **Expected views per pitch:** chance × predicted views × un factor de „reachability”. Canalele mari primesc 30–100 de pitch-uri pe zi, deci șansa să ți-l citească pe al tău e mai mică.

**Coverage chance** vine dintr-un model (`model.py`) antrenat **la fiecare căutare**, pe datele publice YouTube ale genului tău. Modelul ascunde pe rând câte un joc comparabil și învață ce semnale prezic cine l-a acoperit: conținut (cuvinte cheie + model semantic preantrenat MiniLM), jocuri similare jucate, views pe jocuri similare, dacă a mai acceptat key-uri de la dezvoltatori, mărime, engagement și activitate. Tehnic e o regresie logistică cu regularizare L2, cu corecție pentru eroarea de selecție (fiecare canal a fost găsit *printr-un* joc). Dacă datele nu ajung, folosește ponderi implicite și o spune.

**Modelul semantic** (`embed.py`): all-MiniLM-L6-v2 în format ONNX, cuantizat (~23 MB). Se descarcă o singură dată, la prima căutare, sau din Settings → Download now. Dacă nu se poate încărca, aplicația merge mai departe doar cu TF-IDF.

Testele (`tests/test_model.py`, `tests/test_integration.py`) rulează modelul pe „lumi” simulate cu adevăr cunoscut. Verifică că își recunoaște tiparele, că probabilitățile sunt calibrate și că bate ponderile implicite atunci când intuițiile implicite sunt greșite.

## Căutare, sortare și Contacts

- **Results → Search:** filtrează după nume, @handle, email sau un link de canal lipit.
- **Sortare:** din meniul „Sort by” (inclusiv A–Z și după abonați) sau cu click pe orice antet de coloană; al doilea click inversează ordinea (▲/▼).
- **Coloana CONTACTED** arată data la care ai marcat creatorul.
- **Contacts → Have I contacted them?:** lipești orice (link de canal, link de video sau Shorts, @handle, nume) și vezi imediat dacă l-ai contactat. Întâi se caută offline în lista ta; dacă nu-l găsește, întreabă YouTube cine e (1–2 unități; o căutare după nume costă 100). Dacă nu l-ai contactat, îl poți marca direct de acolo, chiar dacă nu a apărut în rezultate.
- Lista de contactați are căutare, notițe („sent key on 12 Oct”), ștergere și export CSV.

## Precizia emailurilor și verificarea manuală

- **Linkuri urmărite:** doar cele din descrierea canalului și cele care se repetă în cel puțin 3 descrieri de video (footer-ul creatorului). Un link care apare într-un singur video (sponsor, pagina unui joc, alt creator) nu e urmărit.
- **Emailuri respinse automat:** helpdesk-uri (zendesk, freshdesk…), platforme și companii de jocuri, plus adrese de tip support@, merch@, orders@, noreply@ etc.
- **Încredere (coloana EMAIL CHECK):**
  - **High:** emailul conține numele canalului, e pe domeniul site-ului propriu sau e scris în descrierea canalului cu context de business.
  - **Medium:** mailbox personal sau agenție, dar fără nume care să se potrivească.
  - **Low · check:** domeniu fără legătură cu creatorul.
- **Verificare:** Show → „Emails to check” aduce coloanele de email în față. **V** = email OK, **X** = email greșit, **O** = deschide pagina pe care a fost găsit. După fiecare decizie trece singur la următorul. Deciziile se țin minte la rulările următoare: emailurile respinse nu mai apar, cele aprobate rămân „✓ verified”.
- Dacă browser fallback-ul nu pornește, motivul exact e scris în `%APPDATA%\CreatorScout\errors.log`.

## De ce lipsește un creator + adăugare manuală

Scrii numele, @handle-ul sau linkul canalului în **Results → Search**. Dacă nu există o potrivire exactă, apare o bară cu:
- **Why isn't it here?:** spune exact unde a căzut canalul. Poate să nu-l fi găsit nicio căutare, să fie în afara intervalului de abonați, să nu fi fost printre cele analizate sau să fie inactiv. Poate fi și exclus pentru că e deja contactat.
- **Add to results:** analizează canalul (~3 unități) și îl bagă în clasament. Apare cu ✚ în față.

Numele jocurilor sunt recunoscute acum și când titlul conține toate cuvintele importante ale numelui (de exemplu „Digging a Hole” → „A Game About Digging A Hole”).

## Keywords & Steam tags

- **Get tags:** lipești linkul de Steam (sau scrii numele) jocului tău și apar tag-urile lui de pe Steam.
- **From comparable games:** adună tag-urile jocurilor comparabile. Numărul de lângă fiecare tag arată câte dintre ele îl au.
- **Click** pe un tag îl selectează sau îl deselectează. **Click dreapta** îl șterge. **+ Add keyword** adaugă unul manual (poți pune mai multe, separate prin virgulă).
- Tag-urile selectate sunt folosite **gratuit** la potrivirea conținutului (TF-IDF + model semantic). Doar dacă bifezi „Also search YouTube…” devin și căutări YouTube, la 100 de unități fiecare.
- **☰ Browse all Steam tags:** lista completă de tag-uri din Steam (~450), cu căutare și filtrul „Selected only”. Click pe un tag îl selectează; tag-urile selectate apar și în lista ta de keywords. Dacă Steam nu poate fi accesat, aplicația folosește o listă de rezervă cu cele mai comune ~140 de tag-uri.
