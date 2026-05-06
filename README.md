# Oh Myself

Standalone terminal AI agent with local tool use.

## Install

```bash
pip install -e .
```

## Run

```bash
ohmy
ohmy -p "inspect this repository"
```

## Experience Library

Inside the interactive REPL:

```text
/exper add [content]   Add a life experience to ~/.ohmyself/experiences/default.md
/exper [question]      Ask using relevant entries retrieved from the local experience library
/exper organize        Classify default entries into topic markdown files
```

## Acknowledgments

This project is based on [OpenHarness](https://github.com/HKUDS/OpenHarness), an open-source agent framework.
