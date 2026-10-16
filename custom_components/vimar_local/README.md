# Vimar Local per Home Assistant

Integrazione personalizzata sperimentale, configurabile dall'interfaccia.
Copia l'intera cartella `vimar_local` in `/config/custom_components/` e riavvia
Home Assistant. In **Impostazioni → Dispositivi e servizi → Aggiungi
integrazione**, cerca **Vimar Local**.

Nel Docker Compose di questo repository la cartella `custom_components` è già
montata: basta riavviare Home Assistant dopo aver aggiunto i file.
L'integrazione non viene aggiunta automaticamente e non occorre modificare YAML.

Inserisci IPv4 locale, password monitor, numero di targhe (1–2), uscite per
targa (1–2), ed eventualmente il PIN separato di apertura. Il numero di uscite
è comune a tutte le targhe; puoi disabilitare le entità non utilizzate.
La topologia è manuale: `attachInfo` non fornisce una discovery affidabile.
La configurazione non verifica la connessione: eventuali errori di password
emergono al primo utilizzo di video o uscite. Per cambiare i parametri,
rimuovi e aggiungi nuovamente l'integrazione (options flow non ancora presente).

## Entità

Con due targhe e due uscite vengono create **11 entità**:

| Entità | Quantità | Comportamento |
| --- | --- | --- |
| Sensore numero targhe configurate | 1 | Configurazione, non rilevamento automatico |
| Pulsante aziona uscita | 4 | Comando reale impulsivo, senza stato aperto/chiuso |
| Evento pressione campanello simulata | 2 | Timestamp dell'ultima simulazione; inizialmente sconosciuto |
| Pulsante simula pressione | 2 | Test locale senza traffico al monitor |
| Telecamera video | 2 | QUII 1 e 2 tramite bridge H.264 locale |

Ogni targa è un dispositivo figlio del monitor. Le entità hanno ID univoci.
I pulsanti uscita inviano `set.device.opendoor` **reale**, una sola volta,
con `door` e `locknumber`; non fanno retry automatici. L'associazione fisica
serratura/cancello e l'attuazione rimangono da verificare. Un timeout non
esclude che l'uscita sia stata azionata. Non viene inventato uno stato di porta.

## Pressioni e automazioni

Premi **Simula pressione campanello** nella targa desiderata. Cambia il
 timestamp della sua entità `event` e il tipo diventa `ring`, con
`simulated: true`. Due pressioni successive producono due aggiornamenti.
Non vengono generati eventi all'avvio né da registrazioni storiche.
Il rilevamento reale delle pressioni e il polling delle registrazioni non sono
collegati in questa versione: le nuove registrazioni non provano una chiamata.

Esempio: usa l'ID effettivo dell'entità visibile nella tua installazione.

```yaml
triggers:
  - trigger: state
    entity_id: event.vimar_targa_1_pressione_campanello_simulata
conditions:
  - condition: template
    value_template: >-
      {{ trigger.to_state is not none
         and trigger.to_state.state not in ['unknown', 'unavailable']
         and trigger.to_state.attributes.get('event_type') == 'ring'
         and trigger.to_state.attributes.get('simulated') == true }}
actions:
  - action: persistent_notification.create
    data:
      title: "Test campanello"
      message: "Pressione SIMULATA della targa 1"
```

## Video

Apri l'entità telecamera oppure aggiungila alla dashboard. La dipendenza
`stream` fornisce HLS e ricava le anteprime dallo stesso flusso. Esempio (adatta l'ID):

```yaml
type: picture-entity
entity: camera.vimar_targa_1_video
camera_view: live
```

Il bridge avvia QUII **solo su richiesta**, decifra H.264 in memoria e lo
converte con FFmpeg in MPEG-TS con PTS/DTS (15 fps assunti) e lo serve a Home Assistant su `127.0.0.1`, porta casuale e percorso con token.
Non richiede pubblicazione di porte Docker aggiuntive e non salva video.
Gli utenti accedono tramite i normali endpoint autenticati delle telecamere HA.
L'URL interno non è destinato ad altri computer e cambia a ogni caricamento.
La libreria `cryptography` è già una dipendenza di Home Assistant; FFmpeg deve
essere disponibile (incluso nell'immagine ufficiale usata da questo progetto).

Limiti della base iniziale:

- Una connessione video per targa: richieste concorrenti allo stesso flusso
  vengono rifiutate con 503. Anteprima e live HA condividono lo stesso lettore.
- Nessun audio; latenza HLS e comportamento HomeKit non ancora verificati.
- Indici QUII 1/2 distinti dai canali delle registrazioni: nessuna associazione
  automatica tra video e campanello viene dedotta.
- La visione contemporanea delle due targhe sul monitor reale resta da provare.
- Non viene mantenuto un sensore di connettività; errori emergono alla richiesta.

La password è conservata nella configurazione di Home Assistant come per le
integrazioni locali. Non esportare `.storage` né i backup con credenziali.
Il certificato autofirmato del monitor è accettato senza verifica CA; usare
solo sulla LAN fidata. Nessuna password o token viene scritto nei log.

## Verifica locale

Dalla radice del repository, con il container di sviluppo già acceso:

```sh
docker exec -i homeassistant-dev python - < tests/test_homeassistant.py
.venv/bin/python -m unittest -q test_vimar_watch.py test_vimar_video.py test_vimar_relay.py
```

I test HA verificano topologia, instradamento delle simulazioni, invio singolo
senza retry, PIN separato, protezione del bridge e decodifica di video sintetico.
Non azionano hardware. Il video sul dispositivo e le aperture reali richiedono
una verifica sul posto.

API di riferimento: [Camera](https://developers.home-assistant.io/docs/core/entity/camera/)
e [Event](https://developers.home-assistant.io/docs/core/entity/event/).
Il protocollo in `protocol/` deriva dagli script di questo repository e dai
moduli QUII vendorizzati; la licenza MIT originale è inclusa in `protocol/LICENSE`.
