# Chat SSE streaming — proxy buffering

InboxAssistant's `POST /api/chat/ask/stream` returns Server-Sent Events. Intermediate
proxies that buffer the response body make tokens arrive in one burst after the
LLM finishes, which defeats streaming.

## Required settings

### FastAPI (already set)

`backend/app/api/web/chat.py` sends:

- `Cache-Control: no-cache, no-transform`
- `Connection: keep-alive`
- `X-Accel-Buffering: no` (tells nginx to skip buffering for this response)

Do **not** add `GZipMiddleware` (or any compression middleware) for this route.
Gzip needs the full body before it can flush.

### nginx

For `/api/chat/` (or at least `/api/chat/ask/stream`), set:

```nginx
proxy_http_version 1.1;
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
proxy_set_header Connection "";
```

`deploy/nginx.conf.template` includes a dedicated `/api/chat/` location with these
directives. See also
https://www.server-sent-events.com/backend-stream-generation-connection-management/python-fastapi-sse-implementation-guide/

### Cloudflare / CDN

Cloudflare buffers ~100 KB by default on some plans. Prefer routing the chat
stream path around the CDN, or enable streaming on the plan. AWS ALB streams by
default.

### Smoke check

```bash
curl -N -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"ping"}' \
  https://HOST/api/chat/ask/stream
```

`-N` disables curl's own buffering. You should see `data:` lines appear
incrementally, not only at the end.
