"""ENGINEERING_GLUE: one streaming archive/Range proxy for fixed source callers."""
from fastapi import HTTPException
from fastapi.responses import StreamingResponse


async def archive_response(client,url,headers,*,timeout=None):
    options={} if timeout is None else {'timeout':timeout}
    upstream=await client.send(client.build_request('GET',url,headers=headers,**options),stream=True)
    if upstream.status_code not in (200,206,416):
        await upstream.aclose();raise HTTPException(503,'Source recording is not ready for transfer')
    async def data():
        try:
            async for chunk in upstream.aiter_bytes():yield chunk
        finally:await upstream.aclose()
    retained={name:upstream.headers[name] for name in ('X-Content-SHA256','Content-Length','Content-Range','Accept-Ranges','ETag','Content-Disposition') if name in upstream.headers}
    return StreamingResponse(data(),status_code=upstream.status_code,media_type='application/x-tar',headers=retained)
