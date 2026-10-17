# moto_autocut

Programma che prende i tuoi video girati in moto (pensato per la **Insta360 Ace Pro 2 sul casco**) e prepara **in automatico un montaggio** da aprire in **DaVinci Resolve** (anche la versione gratuita).

Non è un plugin dentro Resolve. È un programma separato che:

1. guarda tutte le tue clip,
2. decide quali sono i momenti più interessanti,
3. li mette in ordine a tempo di musica,
4. scrive un file che Resolve sa importare (`File → Import → Timeline`).

In Resolve ti ritrovi una timeline già montata, con tutti i tagli modificabili a mano. **Il programma non modifica né cancella i tuoi video originali**: li legge soltanto.

---

## 1. Cosa ti serve

| Cosa | Obbligatorio | Come |
|---|---|---|
| Python 3 | sì | quello che hai già (nel tuo caso 3.13) |
| ffmpeg e ffprobe | sì | vedi sotto |
| Pacchetti Python di base | sì | `pip install -r requirements.txt` |
| `telemetry-parser` | no, ma migliora molto il risultato | vedi sezione 2.3 |
| Una traccia musicale (mp3/wav) | no | senza musica i tagli hanno durata fissa |

### 1.1 Installare ffmpeg su Windows

Apri il **Prompt dei comandi** e scrivi:

```
winget install Gyan.FFmpeg
```

Poi **chiudi e riapri** il Prompt dei comandi (altrimenti non lo trova). Per controllare:

```
ffmpeg -version
ffprobe -version
```

Se entrambi stampano una versione, è a posto.

### 1.2 Installare i pacchetti Python di base

È consigliato usare Python 3.12 a 64 bit. Nella cartella dove hai messo i file del programma, crea e attiva un ambiente virtuale:

```
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
```

Installa i pacchetti di base:

```
python -m pip install numpy scipy librosa==1.0.0 opentimelineio==0.18.1 otio-fcp-adapter==1.0.0
```

Quando riapri il terminale, attiva di nuovo l'ambiente virtuale con `.venv\Scripts\activate` prima di eseguire il programma.

---

## 2. Primo avvio, passo per passo

### 2.1 Prepara una cartella con le clip

Crea una cartella, per esempio `C:\Moto\Giro1`, e copiaci dentro i video (`.mp4`, `.mov`, `.mkv`). Il programma legge **solo i file direttamente in quella cartella**, non nelle sottocartelle.

I file che iniziano per `LRV_` (copie a bassa risoluzione che la camera crea accanto ai video) vengono **saltati** in automatico.

### 2.2 Fai la diagnosi

```
python moto_autocut.py "C:\Moto\Giro1" --analyze-only
```

**Attenzione ai nomi.** Negli esempi qui `C:\Moto\Giro1` e `brano.mp3` sono *esempi*: devi scrivere il percorso vero della tua cartella e del tuo brano. Se scrivi `./clip` e non esiste una cartella `clip`, il programma si ferma con un errore.

Per ogni video stampa una riga come:

```
Analizzo VID_20261017_101530_00_001.mp4 ...
  184s @ 29.97fps | audio: si | camera: Insta360 Ace Pro 2 | campioni IMU: 36800 | feature: gyro, accel, audio
```

Come leggerla:

- `audio: NO` → la clip non ha traccia audio: nel montaggio quel taglio sarà muto.
- `campioni IMU: 0` e `feature: audio` → il programma **non sta leggendo giroscopio e accelerometro**, usa solo l'audio. Funziona, ma il risultato è meno buono.
- `campioni IMU` con un numero grande e `feature: gyro, accel, audio` → sta usando tutto.

### 2.3 Telemetria (giroscopio e accelerometro) – opzionale

Serve il pacchetto `telemetry-parser`. Per Windows a 64 bit esistono pacchetti già pronti solo per **Python 3.9, 3.10, 3.11 e 3.12**, non per il 3.13: per questo con il 3.13 pip prova a compilarlo e fallisce chiedendo Rust.

La soluzione più semplice è installare Python 3.12 accanto al tuo e usarlo con `py -3.12`. La procedura completa, passo per passo, è nel file **`INSTALLARE_TELEMETRY_PARSER.md`**.

Non è garantito che l'Ace Pro 2 sia letto: la libreria dichiara Ace e Ace Pro. Il test pratico è la diagnosi del punto 2.2.

### 2.4 Crea il montaggio

Ci sono tre modi di usarlo. Sostituisci i percorsi con i tuoi.

**a) Durata libera (nessun limite)** – il montaggio dura quanto servono i momenti belli trovati:

```
python moto_autocut.py "C:\Moto\Giro1" -o montaggio
```

**b) Con musica** – i tagli cadono sui beat del brano:

```
python moto_autocut.py "C:\Moto\Giro1" --music "C:\Musica\brano.mp3" -o montaggio
```

La durata la decidono i momenti trovati, ma il montaggio si ferma quando finisce il brano (la musica non viene ripetuta).

**c) Con una durata circa fissa:**

```
python moto_autocut.py "C:\Moto\Giro1" --music "C:\Musica\brano.mp3" --length 90 -o montaggio
```

Significato:

- `"C:\Moto\Giro1"` → la cartella con i video (oppure il percorso di un singolo video).
- `--music ...` → il brano su cui sincronizzare i tagli. Facoltativo.
- `--length 90` → durata desiderata in secondi. **Facoltativo**: se lo togli, non c'è nessun limite. È "circa": l'ultimo taglio può far superare di poco.
- `-o montaggio` → nome dei file prodotti, creati nella cartella da cui lanci il comando.

Non mettere un `\` subito prima delle virgolette finali (`"C:\Moto\"` dà errore nel Prompt di Windows): scrivi `"C:\Moto\Giro1"`.

### 2.5 Cosa stampa e quali file crea

```
Montaggio: 18 tagli, 74.3s (taglio piu' corto 1.5s, piu' lungo 6.0s, medio 4.1s)
  VID_..._001.mp4     10.5s +3.02s  score 0.67
  ...
File creati:
  montaggio.fcpxml
  montaggio.xml
  montaggio.otio
  montaggio_audio.wav
```

Ogni riga è un taglio: **file**, **da che secondo della clip parte**, **quanto dura**, **punteggio** da 0 a 1.

| File | A cosa serve |
|---|---|
| `montaggio.fcpxml` | **Quello da usare per primo.** Video + audio delle clip (il motore) + musica. |
| `montaggio_audio.wav` | Audio già mixato (clip + musica). **Rete di sicurezza**: si trascina in Resolve e si sente sempre. |
| `montaggio.xml` | Alternativa: solo i tagli video, senza audio. |
| `montaggio.otio` | Formato OpenTimelineIO: video, audio delle clip e musica. |

### 2.6 Importa in DaVinci Resolve

1. Apri un progetto in Resolve.
2. `File → Import → Timeline…` e scegli **`montaggio.fcpxml`**.
3. Nella timeline trovi il video, l'audio delle clip (vento e motore) e, se hai usato la musica, la traccia musicale.

Se qualcosa non va:

- **Non si sente l'audio, o una traccia è vuota** → trascina `montaggio_audio.wav` sulla timeline, a partire dall'inizio, e silenzia (o cancella) le tracce audio vuote. Il file ha già tutto mixato e dura esattamente quanto il montaggio.
- **Il `.fcpxml` non si importa** → prova `montaggio.xml` (solo video) e poi trascina il `.wav` sotto; oppure `montaggio.otio`.
- **Le clip risultano "offline"** → tasto destro sulle clip → *Relink Selected Clips* e indica la cartella dei video.
- Il volume della musica e delle clip è scritto nel file (musica a −8 dB di default), ma Resolve potrebbe non applicarlo: se serve, regolalo a mano nella timeline.

> **Importante.** Non ho potuto provare l'importazione dentro DaVinci Resolve. Ho verificato che i file siano ben formati, che durate e tagli tornino e che il `.wav` suoni davvero. Se `.fcpxml` non funziona sul tuo Resolve, copiami il messaggio di errore o descrivi cosa vedi.

---

## 3. Come funziona, esattamente

Tutto il lavoro è diviso in 9 passi. Il tempo viene sempre spezzato in **blocchi da 0,5 secondi**, chiamati *bin*.

### Passo 1 – Elenco delle clip

Il programma prende i file `.mp4 .mov .mkv .insv` nella cartella (o il singolo file che gli passi), in ordine alfabetico. Salta i file nascosti (nome che inizia con `.`) e i proxy `LRV_`. Con `ffprobe` legge di ogni clip **durata, fotogrammi al secondo, risoluzione e se ha l'audio**. Se un file non è leggibile lo salta e lo segnala.

### Passo 2 – Estrazione dei segnali (per ogni clip)

Da ogni clip ricava fino a tre segnali, un valore per ogni bin da 0,5 s.

**a) Giroscopio (`gyro`)** – solo se `telemetry-parser` legge l'IMU.
Calcola la velocità di rotazione totale della camera (la lunghezza del vettore dei tre assi) e ne fa la media in ogni bin. Poi applica un **filtro mediano su 5 bin (2,5 s)**: serve a cancellare i movimenti brevi (un'occhiata allo specchietto dura meno di un secondo e sparisce), mentre una curva vera, che dura di più, resta. Valore alto = la testa/camera sta ruotando in modo sostenuto, tipicamente curve o tratti movimentati.

**b) Accelerometro (`accel`)** – solo se l'IMU è letta.
Calcola l'intensità totale dell'accelerazione e ne prende la **deviazione standard** in ogni bin (quanto "balla"). Valore alto = vibrazioni, buche, frenate e accelerazioni brusche.

**c) Audio (`audio`)** – se la clip ha audio.
Estrae l'audio in mono a 16 kHz, calcola il livello (RMS) in ogni bin e lo converte in decibel. Sul casco il rumore è dominato dal vento, che cresce con la velocità, quindi un livello alto significa in genere **più veloce / più intenso**.

> Nota: il giroscopio sul casco misura come si muove il casco, **non** l'angolo di piega della moto. Per questo viene usato come misura di "movimento sostenuto" e non come angolo.

### Passo 3 – Normalizzazione

I tre segnali hanno unità diverse, quindi vanno resi confrontabili. Per ognuno, mettendo **insieme tutte le clip**, il programma prende il 10° e il 95° percentile: il 10° diventa **0**, il 95° diventa **1**, e tutto viene limitato tra 0 e 1. Così non conta l'unità di misura, e ogni clip viene confrontata con le altre.

### Passo 4 – Punteggio (score)

Per ogni bin lo score è la **media pesata** dei segnali disponibili:

```
score = 0.35 · gyro + 0.30 · accel + 0.35 · audio     (pesi di default)
```

Se per una clip manca un segnale (per esempio niente IMU) i pesi degli altri vengono ricalcolati in modo che sommino a 1. Infine i primi e gli ultimi `--edge-trim` secondi (default 3) di ogni clip vengono messi a 0: sono i momenti in cui parti, ti fermi o tocchi la camera.

### Passo 5 – Trovare i momenti migliori

1. Si usa una finestra di riferimento lunga la media tra `--min-cut` e `--max-cut` (di default 3,75 s).
2. Per ogni clip e per ogni posizione (ogni 0,5 s) si calcola lo score **medio** della finestra.
3. Si ordinano tutte le finestre dalla migliore alla peggiore.
4. Si scelgono dall'alto, **saltando** ogni finestra che nella stessa clip sia più vicina di *finestra + `--min-gap`* secondi a una già scelta (di default 3,75 + 3 = 6,75 s). Questo evita di avere dieci tagli dello stesso identico tornante.

Quanti momenti si tengono dipende da `--length`:

- **Senza `--length`** (nessun limite): si tengono tutti i momenti con score **maggiore o uguale a `--min-score`** (default 0,5). Il montaggio dura quanto la somma dei tagli. Se ne escono troppi, alza `--min-score`; se troppo pochi, abbassalo.
- **Con `--length`**: si prendono i momenti migliori finché la somma delle durate raggiunge la durata richiesta.

### Passo 6 – Durata di ogni taglio (tagli diversi tra loro)

Ogni momento riceve una durata propria, tra `--min-cut` (1,5 s) e `--max-cut` (6 s):

1. Si guarda dove cade il suo score tra il 25° e il 90° percentile di tutti i momenti trovati: da 0 a 1.
2. `durata = min-cut + (max-cut − min-cut) × quel valore`. **I momenti migliori durano di più**, quelli appena sufficienti sono tagli rapidi.
3. Si moltiplica per una variazione casuale tra −50 % e +50 % scalata da `--variety` (default 0,8, cioè circa ±40 %), poi si riporta tra minimo e massimo.

Il caso è "a seme fisso" (`--seed 7`): rilanci e ottieni lo stesso montaggio. Cambia il numero di `--seed` per avere un'altra variante. Con `--variety 0` le durate dipendono solo dallo score.

### Passo 7 – La musica e i beat

Se c'è `--music`, con librosa si trovano i **beat** del brano. Il primo beat diventa l'inizio del montaggio. Ogni taglio dura un **numero pari di beat** (2, 4, 6, 8…), il più vicino alla durata del passo 6 (arrotondato in base al tempo medio tra due beat). I tagli cadono così esattamente sui beat. Quando i beat finiscono, il montaggio si ferma.

Senza musica le durate restano in secondi (arrotondate al centesimo).

### Passo 8 – Ordine e assemblaggio

- `--order chrono` (default): per clip in ordine alfabetico e, dentro la clip, per tempo. Il montaggio segue l'ordine di ripresa.
- `--order build`: dal punteggio più basso al più alto, per un finale in crescendo.

Ogni taglio parte in modo che il momento migliore cada **al centro**, senza uscire dall'inizio o dalla fine della clip. Se è stata data `--length`, si smette al primo taglio che la raggiunge o la supera. Le posizioni dei tagli in fotogrammi si calcolano sul tempo cumulato esatto, così gli arrotondamenti non si accumulano e i tagli restano sui beat.

### Passo 9 – Scrittura dei file

- **`.fcpxml`** (formato Final Cut Pro, versione 1.8): ogni taglio è una clip con il **proprio video e il proprio audio insieme**, quindi il motore e il vento delle clip ci sono. La musica è una clip audio collegata sotto la prima, dal primo beat, per tutta la durata. Il volume (`--clip-db`, `--music-db`) è scritto come regolazione di volume.
- **`.xml`** (Final Cut Pro 7): solo i tagli video, come prima versione compatibile.
- **`.otio`** (OpenTimelineIO): traccia video, traccia con l'audio delle clip e traccia musica.
- **`_audio.wav`**: creato con ffmpeg. Per ogni taglio prende il pezzo esatto di audio della clip (silenzio se la clip non ha audio), li unisce, applica il volume, mixa la musica e protegge dalle distorsioni con un limitatore. Ogni pezzo è forzato alla durata esatta del taglio, quindi l'audio resta sincronizzato anche dopo molti tagli. Formato: 48 kHz, stereo, 16 bit. Si salta con `--no-wav`.

---

## 4. Tutte le opzioni

| Opzione | Default | Cosa fa |
|---|---|---|
| `folder` (primo argomento) | – | Cartella con i video, oppure un singolo video. Obbligatorio. |
| `--music FILE` | nessuna | Brano su cui sincronizzare i tagli. |
| `-o`, `--out NOME` | `montaggio` | Nome dei file in uscita, senza estensione. |
| `--length SEC` | nessuno | Durata circa desiderata. Se omesso, nessun limite. |
| `--min-score X` | 0.5 | Senza `--length`: score minimo (0-1) per tenere un momento. |
| `--min-cut SEC` | 1.5 | Durata minima di un taglio (con la musica, arrotondata ai beat verso l'alto). |
| `--max-cut SEC` | 6.0 | Durata massima di un taglio. |
| `--variety X` | 0.8 | Variazione casuale delle durate (0 = solo in base allo score). |
| `--seed N` | 7 | Cambia numero per un'altra variante del montaggio. |
| `--min-gap SEC` | 3.0 | Distanza minima tra due scelte nella stessa clip. |
| `--edge-trim SEC` | 3.0 | Secondi scartati a inizio e fine di ogni clip. |
| `--order` | `chrono` | `chrono` = ordine di ripresa, `build` = intensità crescente. |
| `--music-db X` | -8 | Volume della musica in dB (più basso = più piano). |
| `--clip-db X` | 0 | Volume dell'audio delle clip in dB. |
| `--no-wav` | spento | Non crea l'audio mixato `.wav`. |
| `--w-gyro` | 0.35 | Peso della rotazione (curve). |
| `--w-accel` | 0.30 | Peso di vibrazioni/frenate. |
| `--w-audio` | 0.35 | Peso del livello audio (velocità/vento). |
| `--analyze-only` | spento | Solo diagnosi: non crea nessun file. |

### Ricette

- **Montaggio senza limite di durata:** non scrivere `--length`.
- **Più momenti (montaggio più lungo):** `--min-score 0.4`. **Meno momenti (più selettivo):** `--min-score 0.65`.
- **Tagli ancora più vari:** `--variety 1 --min-cut 1 --max-cut 8`.
- **Tagli più omogenei:** `--variety 0 --min-cut 2.5 --max-cut 4`.
- **Montaggio più nervoso:** `--max-cut 3`.
- **Un'altra variante dello stesso montaggio:** `--seed 8` (o 9, 10…).
- **Musica più forte o più piano:** `--music-db -3` / `--music-db -14`.
- **Motore e vento più bassi:** `--clip-db -6`.
- **Solo i tratti veloci:** `--w-audio 1 --w-gyro 0 --w-accel 0`.
- **Solo curve e frenate:** `--w-audio 0`.
- **Finale in crescendo:** `--order build`.

---

## 5. Limiti noti (cose che il programma NON fa)

- **Non usa il GPS.** Con una traccia GPS si riconoscerebbero meglio curve e velocità, ma non è ancora implementato.
- **Non guarda le immagini.** Non riconosce panorami, altre moto, o riprese sfocate: decide solo da movimento e audio.
- **Lo score è un'euristica.** Con la camera sul casco non esiste una formula perfetta: pesi, soglia e durate vanno ritoccati guardando i primi risultati.
- **Con la musica il montaggio non supera il brano**: i beat finiscono con la musica e il montaggio si ferma lì.
- **Il vento sul casco è forte**: l'audio delle clip può coprire la musica. Abbassa con `--clip-db`.
- **Non stabilizza.** Per l'orizzonte stabile usa Gyroflow, separatamente.
- **Non è stato provato su file reali dell'Ace Pro 2 né dentro DaVinci Resolve** al momento della scrittura: è testato su clip e telemetria simulate. La prima diagnosi sui tuoi file è il vero test.
- Clip con fotogrammi al secondo diversi nello stesso montaggio funzionano ma sono la situazione meno testata. La timeline usa gli fps della prima clip scelta.

---

## 6. Problemi comuni

| Messaggio / sintomo | Causa | Soluzione |
|---|---|---|
| `ModuleNotFoundError: No module named 'scipy'` (o altro modulo) | Pacchetti non installati | `pip install -r requirements.txt` (con lo stesso Python con cui lanci il programma) |
| `ERRORE: non trovo ffmpeg e ffprobe` | ffmpeg non installato o Prompt non riaperto | `winget install Gyan.FFmpeg`, poi chiudi e riapri il Prompt |
| `ERRORE: il percorso '...' non esiste` | Percorso sbagliato | Usa il percorso vero, tra virgolette; controlla lettera del disco e nome |
| `ERRORE: il file musicale '...' non esiste` | Percorso del brano sbagliato | Usa il percorso vero, oppure togli `--music` |
| `ERRORE: nessun video trovato` | Cartella vuota o video in una sottocartella | Metti i video direttamente nella cartella |
| `Nessun momento con score >= 0.5` | Soglia troppo alta per i tuoi video | `--min-score 0.35`, oppure usa `--length` |
| Montaggio troppo lungo | Troppi momenti sopra soglia | Alza `--min-score` (0.6, 0.7) oppure dai `--length` |
| Montaggio troppo corto con la musica | Il brano è finito | Usa un brano più lungo |
| Tagli ancora troppo simili | Variazione bassa o intervallo stretto | `--variety 1` e allarga `--min-cut` / `--max-cut` |
| In Resolve non si sente la musica o il motore | Importazione dell'audio non riuscita, o tracce silenziate | Trascina `montaggio_audio.wav` sulla timeline; controlla che le tracce audio non siano mute |
| `[imu] telemetry-parser non installato` | Pacchetto assente | È solo un avviso: si usa solo l'audio. Vedi `INSTALLARE_TELEMETRY_PARSER.md` |
| `campioni IMU: 0` con il pacchetto installato | Il parser non legge quel file | Registra in FreeFrame, oppure apri una clip in Gyroflow: se lì vedi i grafici i dati ci sono |
| `audio: NO` nella diagnosi | La clip non ha traccia audio | Nel montaggio quel taglio sarà muto (nel `.wav` c'è silenzio) |
| `(audio mixato non creato: ...)` | ffmpeg non è riuscito a creare il `.wav` | Copia il messaggio; i file `.fcpxml/.otio` sono comunque creati |
| Nessun beat rilevato | Brano senza ritmo marcato | Prova un altro brano, oppure togli `--music` |
| In Resolve le clip sono offline | Resolve non ha ritrovato i file | Tasto destro sulle clip → Relink, e indica la cartella dei video |

Se compare un errore diverso da questi, copia tutto il testo del messaggio così come lo vedi.

---

## 7. Dove si trova cosa nel codice

Tutto è in `moto_autocut.py`:

| Funzione | Cosa fa |
|---|---|
| `probe` / `probe_audio` | Durata, fps, risoluzione e audio con ffprobe |
| `read_imu` | Legge giroscopio e accelerometro con telemetry-parser |
| `read_audio` | Estrae l'audio con ffmpeg |
| `extract_features` | Calcola `gyro`, `accel`, `audio` per ogni bin |
| `normalize_and_score` | Normalizza (10°–95° percentile) e calcola lo score |
| `beat_times` | Trova i beat della musica |
| `find_peaks` | Trova i momenti migliori distanziati |
| `assign_durations` | Dà a ogni momento la sua durata |
| `assemble` | Ordina i momenti, li aggancia ai beat e crea i tagli |
| `frame_bounds` | Posizioni esatte dei tagli in fotogrammi |
| `export_fcpxml` | Scrive il `.fcpxml` (video + audio + musica) |
| `export_otio` | Scrive `.otio` e `.xml` |
| `render_audio` | Crea l'audio mixato `.wav` con ffmpeg |
| `main` | Controlli iniziali, opzioni da riga di comando, ordine delle operazioni |

La risoluzione di 0,5 s è la costante `DT` in cima al file.
