# Integrare un videocitofono Vimar in Homekit

![Descrizione immagine](./assets/logo.jpg)

## Obbiettivo
Ho un videocitofono della Vimar comprato su amazon (K42957) che funziona tramite app (anche se alcune funzionalità sono risicate) ma che 
non risulta integrabile con nessun sistema in commercio (Homekit, Alexa, HomeAssisant ecc...)
Mi è quindi venuto il tarlo di cercare di farlo andare con HomeAssisnat in modo da poterlo poi "convertire" in una videocamera HomeKit.

## Come procedere
Per fare ciò è necessario seguire alcuni step:
- Individuare l'indirizzo Ip del dispositivo nella propria rete (nel mio caso, tramite il firtzbox)
- Impostare un ip statico per quel dispositivo altrimenti se rimane in DHCP è inutile procedere
- Conoscere la password del dispositivo (di default è quella scritta nell'etichetta dietro). Se l'avete cambiata è quella impostata nell'app Vimar View Door
- Conoscere un pò di informatica

## Testing phase
Prototipo verificato il 16 ottobre 2026 sul monitor `10.0.1.XX`, hardware
**IDS9457AW**, firmware **V100.R001.A344.00.G0078.B003**.

## Risultati

- Autenticazione solo via HTTPS (certificato locale, self signed). La password iniziale dell'etichetta non era più valida.
- `get.record.session` / `get.record.message`: lettura registrazioni riuscita.
- Ho fatto tre pressioni esterne confermate dall'utente corrispondono a video sul
  canale **2**, orari del dispositivo 14:50:07, 14:53:08, 14:56:49.
  Sono etichettati `occurtype=manual` anche quando causati dal campanello.
- Il watcher ha emesso un solo `record_added` nella seconda prova, senza
  duplicati nelle letture successive.
- `get.device.status/calling` è rimasto `false` nella terza prova:
  non viene usato come trigger affidabile.
- `get.device.streamkey`: chiave media recuperata e usata solo in memoria.
- **Video QUII su TCP 34567**, AES-256, H.264 **480×280**: 87 fotogrammi
  acquisiti e decodificati da FFmpeg. Visibile la telecamera esterna.
- Indice QUII testato **1**, distinto dal canale registrazioni **2**.
- Verificato anche **QUII 2**: 101 fotogrammi ricevuti e JPEG decodificato,
  con vista del vialetto/giardino distinta dalla strada mostrata da QUII 1.
  Non è ancora verificata la corrispondenza completa tra gli indici video
  e i canali delle registrazioni delle due targhe.
- Maledetti voi e quelli di Vimar/Qualvision che usate un fottuto protocollo video proprietario quando c'è un bellissimo RTSP utilizzabile senza problemi.

## Uso

Python 3.10+. Il watcher non richiede librerie aggiuntive; il video richiede
`cryptography`, ffplay per il live e ffmpeg per estrarre JPEG.
Su questo Mac sono già disponibili. Per un nuovo ambiente:

```sh
cd /Users/marcodalprato/GitHub/vimar_wifi_hack
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

I comandi chiedono la password con input nascosto e non la salvano.
Per uso non interattivo accettano `VIMAR_PASSWORD`, da fornire tramite il
proprio gestore di segreti, senza inserirla nei file versionati.

### Alert da terminale

```sh
python3 vimar_watch.py --duration 0 --bell
```

Controlla i video sul canale 2 ogni 10 secondi. Stampa una baseline, poi
`record_added` con `possible_ring=true` ed emette il beep del terminale
(se abilitato nelle sue preferenze). Ctrl+C interrompe.

```sh
python3 vimar_watch.py --once
python3 vimar_watch.py --duration 120 --interval 5 --verbose
```

**Limiti:** rileva nuove registrazioni, non un evento nativo del campanello.
Richiede registrazione automatica attiva e spazio disponibile. Potrebbe
avvisare anche per una registrazione manuale: questo falso positivo non è
stato escluso. La latenza comprende salvataggio del video e polling.
La baseline iniziale non genera alert; il riavvio non recupera eventi persi.
Se una prima lista vuota nasconde vecchi file che appaiono successivamente,
può generare un falso positivo. Un errore di rete/protocollo interrompe il
processo con exit code 1; riconnessione automatica non implementata.
La ricerca riguarda il giorno corrente.

Il firmware aggiunge `Z` anche ad orari apparentemente locali: `starttime`
è mantenuto come dato del dispositivo, non interpretato come UTC certo.

### Video live

```sh
python3 vimar_video.py --live --seconds 0
```

Per la seconda targa esterna:

```sh
python3 vimar_video.py --channel 2 --live --seconds 0
```

`--channel 1` mostra la prima telecamera (strada), `--channel 2` la seconda
(vialetto/giardino). Verificate singolarmente; streaming simultaneo non testato.

Apre ffplay senza salvare il video. Chiudere la finestra o premere Ctrl+C.
Non è un URL RTSP nativo: lo script passa il flusso QUII decodificato al
lettore come H.264. Audio non implementato. Uso simultaneo prolungato con
l'app e affidabilità 24/7 non verificati.

### Cattura e immagine

```sh
python3 vimar_video.py --seconds 10 --output output/prova.h264 --snapshot output/prova.jpg
```

Non sovrascrive file esistenti. Estrae il primo fotogramma. Output esclusi
 da Git e creati con permessi riservati. H.264 grezzo, senza timing di un
contenitore MP4. Video/immagine di verifica in `output/verified-live.*`.

## Verifica

```sh
python3 vimar_probe.py --password-prompt
python3 -m unittest -v test_vimar_watch.py test_vimar_video.py
```

Probe senza password: errore applicativo 401 su HTTP 200. TLS senza verifica
CA per il certificato autofirmato del monitor; usare su LAN fidata. Proxy e
redirect disabilitati. Nessun comando di apertura serrature o configurazione.

## Riferimenti

- [Manuale Vimar](https://www.vimar.com/irj/go/km/docs/z_catalogo/DOCUMENT/49401950C0_01_K42947_K42957_M01624.113264.pdf):
  indica 443 per configurazione e 8443 per streaming. Verificata anche 34567.
- [Allo wT7](https://github.com/fdaneluzzi/homeassistant-allo-wt7), commit
  `7a6bd7c621911b6b841b9e0d47561c9fa882dbf0`: envelope XML e registrazioni.
- [quii-lan-client](https://github.com/fariborz0015/quii-lan-client), commit
  `f640cd36072056def2449984b49592f73738c1d9`: moduli media in `vendor/quii_lan`.
  Licenza MIT conservata. Nessun modulo web o talk eseguito.
- [qualvision-tools](https://github.com/dpeddi/qualvision-tools): ricerca su
  altri kit Vimar e `get.device.status`.
- [link-amazon](https://www.amazon.it/K42957-supplementare-videocitofonico-alimentatore-fissaggio/dp/B0CVN4RY8V): è quello che ho comprato io.

