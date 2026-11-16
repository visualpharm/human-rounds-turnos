---
title: Native image inputs for the MCP order-analysis tool
scope: repo
area: integrations
tags: [mcp, chatgpt, files, order-analysis]
created: 2026-07-25
refs: []
status: active
---

## Problem

`analyze_order` accepted only a base64 string. Chat assistants could see an
order scan attached to the conversation but could not pass that attachment to
the MCP server as a normal file, and large base64 arguments exceeded the MCP
request limit.

## Root cause

MCP tool arguments are JSON; image content blocks are not tool arguments.
ChatGPT binds conversation attachments only when the tool advertises a
top-level file object through `_meta["openai/fileParams"]`.

## Solution

Expose a `file` argument with the exact ChatGPT file shape
(`download_url`, `file_id`, optional `mime_type` and `file_name`) and retain
`data_b64` as a compatibility fallback. Resolve the temporary HTTPS URL on the
server, restrict it to the OpenAI file CDN (including redirects), cap the read
at `MAX_ORDEN_BYTES`, then feed the bytes into the existing
`solicitudes.analizar_bytes` pipeline.

## How to apply

For future MCP tools that consume ChatGPT attachments, use the complete
four-property schema and require only `download_url` plus `file_id`; incomplete
schemas fail tool scanning. Never accept an arbitrary download host or trust
declared MIME/filename over the existing magic-byte handling.
