# Entscheidungen und Abweichungen von SPEC.md

CLAUDE.md verlangt: Abweichungen von der SPEC werden hier eingetragen, nicht
stillschweigend gemacht. Jeder Eintrag nennt den Grund und was stattdessen gilt.

---

## D1 — Kein `ANTHROPIC_API_KEY`; die Agenten sprechen über `zellwerk-llm`

**SPEC §4** nennt „Anthropic Python SDK (Claude API), Modell per `.env`" und
**§5** ein `.env.example` mit `ANTHROPIC_API_KEY`.

**Stattdessen:** Es gibt keinen API-Key. Der Zugang läuft über das
Claude-Max-Abo des Betreibers, vermittelt durch den Dienst `zellwerk-llm`
(`packages/../services/zellwerk-llm`). Das Anthropic-SDK bleibt trotzdem die
Bibliothek der Wahl — es wird lediglich umgebogen:

```python
from anthropic import Anthropic
client = Anthropic(base_url=os.environ["ZW_LLM_BASE_URL"], api_key="unused")
```

Der Proxy bedient `POST /v1/messages` im Anthropic-Format, das SDK merkt keinen
Unterschied. `.env.example` führt deshalb `ZW_LLM_BASE_URL` statt
`ANTHROPIC_API_KEY`.

**Grund:** Bewusste Entscheidung des Betreibers gegen einen API-Schlüssel. Zusätzlich entfällt damit ein Secret im Repo.

**Wichtig — der Proxy refresht NIE selbst.** Ein Refresh rotiert den
Refresh-Token und widerruft den alten serverseitig; das hat in der Praxis
schon einmal einen Dienst für Stunden lahmgelegt. `zellwerk-llm` ist ein rein
passiver Leser, der Token wird von außen hineingereicht. Details stehen im
Kopfkommentar von `services/zellwerk-llm/proxy.py`.

---

## D2 — Formierungs-Templates werden synthetisch erzeugt, nicht heruntergeladen

**SPEC §7.2** verlangt ein Skript `data/build_templates.py`, das den
Severson-et-al.-Datensatz von `https://data.matr.io/1/` lädt und daraus 5–10
Erstzyklus-Profile ableitet.

**Stattdessen:** `data/build_templates.py` erzeugt die Profile **synthetisch**
aus den in der Literatur veröffentlichten Kennwerten (Spannungsplateaus,
C/10-Erstladung, typische Kapazitätsstreuung). Die Kurvenformen sind als
Modellkurven gekennzeichnet, nicht als Messdaten.

**Grund:** Zwei unabhängige Gründe, beide belastbar:

1. *Technisch:* Die zellwerk-Dienste laufen im Compose-Netz `backend`, das mit
   `internal: true` bewusst keinen Internetzugang hat. Ein Download ist dort
   nicht möglich, und dem Netz für einen einmaligen Datenabzug ein Loch zu
   bohren, widerspricht dem Isolationsentwurf.
2. *Inhaltlich:* Der Datensatz besteht aus LFP/Graphit-Rundzellen unter
   Schnellladeprotokollen. Die Musterfabrik simuliert eine C/10-Erstformierung.
   Die Zyklen wären also ohnehin umzurechnen gewesen — der „echte Datensatz"
   hätte Authentizität suggeriert, die die abgeleiteten Kurven nicht haben.

**Konsequenz für die Demo:** Nirgends darf behauptet werden, die Kurven stammten
aus dem Severson-Datensatz. Sie sind plausible Modellkurven — genau wie die
Prozessparameter in §7.1 ausdrücklich „plausible Lehrbuch-Defaults" sind.

---

## D3 — Eigener Compose-Stack, isoliert vom Nachbarprojekt

zellwerk läuft in einer Docker-in-Docker-Umgebung, in der bereits ein anderes
Projekt mit eigenem Dashboard betrieben wird. zellwerk bekommt deshalb einen
**eigenen** Compose-Stack (Projektname `zellwerk`, eigene Netze, eigene
Volumes) statt sich in den bestehenden einzuhängen.

**Grund:** Das Nachbarprojekt muss durchgehend benutzbar bleiben. Ein eigener
Stack lässt sich unabhängig starten, stoppen und neu bauen, ohne es
anzufassen.

**Netzentwurf:**

| Netz | Eigenschaft | Wer hängt dran |
|---|---|---|
| `backend` | `internal: true` — kein Internet | alle zellwerk-Dienste |
| `egress` | normal | ausschließlich `zellwerk-llm` |

`zellwerk-llm` hat je ein Bein in beiden Netzen und ist damit der einzige
kontrollierte Weg nach draußen — dasselbe Muster, das ein Reverse-Proxy für die
Gegenrichtung benutzt. Kein anderer Dienst erreicht das Internet, auch nicht
versehentlich.

---

## D4 — Ingest-Zielrate wird gemessen, nicht behauptet

**SPEC §8.2** nennt „Ziel: ≥ 5.000 Werte/s auf Entwickler-Hardware".

Die Musterfabrik erzeugt im Normalbetrieb rund 30–60 Werte/s (sechs Stationen,
1-s-Takt, Formierung 10 s). Die 5.000 Werte/s sind also eine
*Kapazitäts*anforderung an den Ingest-Pfad, keine Eigenschaft des laufenden
Systems. Sie wird deshalb mit einem eigenen Lastgenerator gemessen
(`tests/load/ingest_load.py`) und das **gemessene** Ergebnis in der README
dokumentiert — inklusive der Hardware, auf der es gemessen wurde.

Eine Zahl, die nie unter Last geprüft wurde, ist keine erfüllte Anforderung.
