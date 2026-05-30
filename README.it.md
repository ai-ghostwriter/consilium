# Consilium 🏛️🤖⚔️🤖

> Un loop di dibattito strutturato tra due agenti AI pari grado — **Claude Code**
> e **Codex** — che discutono un argomento fino a convergenza, arbitrati da un
> giudice indipendente. *Consilium* (latino: il consiglio che delibera) trasforma
> due modelli in un organo deliberante che ti consegna una sola decisione.

*[🇬🇧 English version](README.md)*

Due modelli di frontiera raramente fanno gli stessi errori. **Consilium**
trasforma questo in un vantaggio: fa criticare a Claude e Codex il ragionamento
l'uno dell'altro per più round, poi un giudice neutrale decide quando hanno
*davvero* convergito — non quando si stanno solo facendo i complimenti.
L'output è una singola decisione operativa.

È confezionato come [plugin per Claude Code](https://docs.claude.com/en/docs/claude-code/plugins)
(e skill), ma l'orchestratore (`skills/consilium/scripts/consilium.py`) è uno
script Python autonomo eseguibile da qualsiasi shell.

## Perché usarlo

- **Riduce i punti ciechi del singolo modello.** Ognuno attacca i punti deboli
  dell'altro.
- **Niente falsa convergenza.** Si ferma in anticipo solo quando *entrambi* i
  debater danno conciliabilità ≥ 85 **e** un giudice indipendente è d'accordo.
- **Giudizio bilanciato tra vendor.** La modalità `panel` opzionale affianca un
  giudice Anthropic a uno OpenAI, così il bias di famiglia si annulla.
- **Validazione a costo zero.** Lo smoke test gira offline con CLI fittizi —
  nessuna chiamata API, nessun token.
- **Zero dipendenze.** Solo libreria standard Python.

## Come funziona

Ogni round: entrambi i debater criticano l'avversario, integrano ciò che è valido,
emettono una posizione rivista e un self-score. Un giudice decide sulla
convergenza. Tripla condizione di stop: **budget** (max round) + **self-score** +
**verdetto del giudice**. La sintesi finale (Opus) produce un'unica decisione
operativa.

I due debater **non si parlano mai direttamente**: tutto passa dallo stato
condiviso `debate_state.json`.

## Installazione come plugin

Consilium è elencato nella marketplace `codex-coprocessor` insieme agli altri
strumenti Claude+Codex.

```text
# dentro Claude Code:
/plugin marketplace add ai-ghostwriter/codex-coprocessor
/plugin install consilium@codex-coprocessor
```

## Avvio rapido (script standalone)

```bash
# 1. Verifica che i due CLI siano installati e autenticati
claude --version
codex --version

# 2. Lancia un dibattito
python skills/consilium/scripts/consilium.py "Conviene migrare l'API da REST a gRPC?" --lang Italian

# 3. (opzionale) Giudizio cross-vendor a massima neutralità
python skills/consilium/scripts/consilium.py "Postgres o DynamoDB per questo carico?" \
    --judge-mode panel --repo-path /path/to/repo --lang Italian
```

stdout = la decisione operativa finale. La trascrizione completa è salvata in
`debate_state.json` nella cartella di lavoro.

### Flag principali

| Flag | Default | A cosa serve |
|------|---------|--------------|
| `--max-rounds` | `5` | Tetto massimo di round |
| `--threshold` | `85` | Self-score per lo stop anticipato |
| `--judge-mode` | `haiku` | `haiku` \| `codex` \| `alternate` \| `panel` |
| `--workdir` | tempdir | Dove salvare stato e log |
| `--repo-path` | nessuno | Codebase che i debater possono leggere |
| `--lang` | `English` | Lingua del contenuto e della decisione finale |

Vedi [`skills/consilium/SKILL.md`](skills/consilium/SKILL.md) per il riferimento
completo del comportamento, inclusa la riconciliazione in modalità panel.

## Requisiti

- Python 3.9+ (solo libreria standard)
- CLI [`claude`](https://docs.claude.com/en/docs/claude-code), autenticato
- CLI [`codex`](https://github.com/openai/codex), autenticato

## Installazione manuale (solo skill, senza plugin)

```bash
git clone https://github.com/ai-ghostwriter/consilium.git ~/dev/consilium
ln -s ~/dev/consilium/skills/consilium ~/.claude/skills/consilium
```

Poi chiedi a Claude Code di "far dibattere Claude e Codex su X".

## Test

```bash
bash skills/consilium/scripts/test_smoke.sh   # offline, CLI fittizi, nessun token speso
```

Atteso: `ALL TESTS PASSED`. La CI lo esegue a ogni push.

## Licenza

[MIT](LICENSE)
