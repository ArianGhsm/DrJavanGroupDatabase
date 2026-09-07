# ADR-005 — Production hardening
Status: Accepted — Stage 5
Telegram updates now use leased claim/complete/release; polling never advances past a failed update. Rate limiting is serialized and bot state uses WAL/pruning. Reindex keeps atomic DB swap and adds POSIX flock. Runtime DeepSeek V4 explicitly sends `thinking: {type: disabled}` because AvalAI documents thinking as default and low/medium reasoning effort mapping to high. Corrupt cache rows become misses. Telegram HTML failures fall back to plain text. Deployment is isolated under Unix user/service `drjavanbot`; secrets are outside Git.
