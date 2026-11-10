# Meesterslijpers – OpenAI Ads productfeed

Zet de Google XML-feed van meesterslijpers.nl dagelijks om naar het OpenAI Ads-formaat (CSV) en publiceert het resultaat via GitHub Pages en/of SFTP.

## Bestanden

| Bestand | Plek in de repo | Wat het doet |
|---|---|---|
| `convert.py` | root | Haalt `https://www.meesterslijpers.nl/feed/google.xml` op en schrijft `dist/meesterslijpers_openai.csv`, `.csv.gz` en `report.txt` |
| `sftp_upload.py` | root | Uploadt de `.csv.gz` naar de OpenAI SFTP-root (alleen als secrets zijn ingesteld) |
| `feed.yml` | `.github/workflows/feed.yml` | Draait dagelijks om 03:17 UTC, en handmatig via "Run workflow" |

## Installatie

1. Maak een nieuwe **openbare** repo `meesterslijpers-openai-feed` (GitHub Pages werkt op het gratis plan alleen met openbare repo's).
2. Zet `convert.py`, `sftp_upload.py` en `README.md` in de root en `feed.yml` in `.github/workflows/`.
3. Settings → Pages → Source: **GitHub Actions**.
4. Actions → "OpenAI Ads feed – Meesterslijpers" → **Run workflow**.
5. Feed-URL na de eerste run: `https://<github-gebruiker>.github.io/meesterslijpers-openai-feed/meesterslijpers_openai.csv`

### Optioneel: SFTP

Secrets in Settings → Secrets and variables → Actions: `OPENAI_SFTP_HOST`, `OPENAI_SFTP_USER`, `OPENAI_SFTP_PASSWORD`, optioneel `OPENAI_SFTP_PORT`. De workflow uploadt dan elke dag `meesterslijpers_openai.csv.gz` naar de root.

## Wat de converter doet

- Mapt de `g:`-velden naar OpenAI-veldnamen en vult `seller_name` en `seller_url`.
- **Merk:** producten zonder merk worden overgeslagen (geen vast merk invullen bij een winkel met veel merken).
- **Dubbele ID's:** alle producten met een gedeeld ID worden overgeslagen, zodat er niet per dag een ander product onder hetzelfde ID verschijnt.
- **Actieprijs:** `sale_price` alleen als die lager is dan de prijs én binnen de actieperiode valt.
- **URL's:** spaties worden `%20`, `http://` wordt `https://`.
- Prijzen als `119.00 EUR`, ongeldige GTIN's weggelaten, HTML uit teksten.
- **Uitverkocht:** de bronfeed bevat alleen producten op voorraad. Een product dat verdwijnt, wordt nog 14 dagen gemeld als `out_of_stock` (`is_ads_eligible=false`), zodat OpenAI het niet blijft tonen. Die lijst wordt tussen runs bewaard in de Actions-cache (`state/`).
- **Veiligheidsremmen:** niets publiceren bij minder dan 5.000 eligible producten, of bij een daling van meer dan 20% ten opzichte van de vorige run. De vorige feed blijft dan staan en GitHub mailt dat de run is mislukt.
  Is de daling terecht (bijv. bewust assortiment opgeschoond)? Start de workflow dan handmatig met "Maximale daling" = `100`.
- **Keepalive:** is de laatste wijziging 45+ dagen oud, dan maakt de taak een lege commit, zodat GitHub de geplande taak niet uitzet.

## Instellingen (environment variables)

| Variabele | Standaard | Betekenis |
|---|---|---|
| `INCLUDE_BRANDS` | leeg (alle) | Bijv. `Kazoku,Global` om alleen die merken te sturen |
| `BRAND_FALLBACK` | leeg | Leeg = producten zonder merk overslaan |
| `KEEP_OUT_OF_STOCK` | `true` | `false` = verdwenen/uitverkochte producten niet melden |
| `MISSING_DAYS` | `14` | Dagen dat een verdwenen product als uitverkocht wordt gemeld |
| `MIN_PRODUCTS` | `5000` | Minimum aantal eligible producten om te publiceren |
| `MAX_DROP_PCT` | `20` | Maximale daling t.o.v. vorige run |

## Controle

Na elke run staat het rapport in de samenvatting van de Actions-run en op `.../report.txt`: aantallen, merken, overgeslagen rijen en meldingen.
