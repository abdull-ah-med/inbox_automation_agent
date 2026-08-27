import { useEffect, useRef, useState } from "react"

import { cn } from "@/lib/utils"

const EMAIL_CSP =
  "script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; img-src https: data:; style-src 'unsafe-inline'; font-src https: data:"

const SANDBOX = "allow-popups allow-popups-to-escape-sandbox allow-same-origin"

export const isHtmlEmailSupported = (): boolean => {
  if (typeof document === "undefined") return false
  const iframe = document.createElement("iframe")
  return "sandbox" in iframe && "srcdoc" in iframe
}

export const wrapEmailHtml = (bodyHtml: string): string => {
  // Reading-pane chrome inspired by Outlook on the web (Segoe UI / Calibri, white paper).
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${EMAIL_CSP}"><base target="_blank" rel="noopener noreferrer"><style>
html{background:#fff}
body{margin:0;padding:16px 20px;min-height:40px;height:auto!important;overflow:hidden;background:#fff;color:#242424;font:15px/1.5 "Segoe UI",Calibri,"Segoe UI Emoji","Segoe UI Symbol",Arial,sans-serif;word-wrap:break-word;overflow-wrap:anywhere}
img{max-width:100%;height:auto}
a[href]{color:#0563c1;text-decoration:underline}
table{border-collapse:collapse;max-width:100%}
td,th{word-wrap:break-word}
blockquote{margin:0.5em 0 0.5em 0.8ex;border-left:3px solid #ccc;padding-left:1ex;color:#605e5c}
blockquote[type=cite]{margin:0.5em 0 0.5em 0.8ex;border-left:3px solid #ccc;padding-left:1ex;color:#605e5c}
pre,code{font-family:Consolas,"Courier New",monospace;white-space:pre-wrap;word-wrap:break-word}
p{margin:0 0 0.85em}
hr{border:0;border-top:1px solid #edebe9;margin:1em 0}
div.WordSection1,div[class*="WordSection"]{page:WordSection1}
</style></head><body>${bodyHtml}</body></html>`
}

type HtmlEmailFrameProps = {
  html: string
  title: string
  className?: string
}

export const HtmlEmailFrame = ({ html, title, className }: HtmlEmailFrameProps) => {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(120)
  const srcDoc = wrapEmailHtml(html)

  useEffect(() => {
    const iframe = iframeRef.current
    if (!iframe) return

    let observer: ResizeObserver | null = null

    const measure = () => {
      const doc = iframe.contentDocument
      if (!doc?.body) return
      const next = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight, 40)
      setHeight(next)
    }

    const handleLoad = () => {
      measure()
      observer?.disconnect()
      observer = null
      const doc = iframe.contentDocument
      if (!doc?.body || typeof ResizeObserver === "undefined") return
      observer = new ResizeObserver(() => {
        measure()
      })
      observer.observe(doc.body)
    }

    iframe.addEventListener("load", handleLoad)
    if (iframe.contentDocument?.readyState === "complete") {
      handleLoad()
    }

    return () => {
      iframe.removeEventListener("load", handleLoad)
      observer?.disconnect()
    }
  }, [])

  return (
    <iframe
      ref={iframeRef}
      title={title}
      srcDoc={srcDoc}
      sandbox={SANDBOX}
      className={cn("w-full border-0 bg-white", className)}
      style={{ height }}
    />
  )
}
