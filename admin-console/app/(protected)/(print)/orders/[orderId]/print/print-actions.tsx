"use client"

import { useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Printer, X } from "lucide-react"

const PX_PER_MM = 3.7795 // 96dpi CSS px → mm
const STYLE_ID = "dynamic-page-size"

/**
 * Measures the receipt and injects a @page rule sized to its content.
 * Avoids the "blank tail" or "Letter fallback" you get with a static
 * height. Re-runs before each print so a re-print after layout shifts
 * still gets a tight page.
 */
function setPageSizeToReceipt() {
  const el = document.querySelector<HTMLElement>("[data-receipt]")
  if (!el) return
  const heightMm = Math.ceil(el.offsetHeight / PX_PER_MM) + 4 // small buffer for the cutter
  const css = `@page { size: 80mm ${heightMm}mm; margin: 0; }`
  let tag = document.getElementById(STYLE_ID) as HTMLStyleElement | null
  if (!tag) {
    tag = document.createElement("style")
    tag.id = STYLE_ID
    document.head.appendChild(tag)
  }
  tag.textContent = css
}

function printReceipt() {
  setPageSizeToReceipt()
  window.print()
}

export function PrintActions() {
  // Open the browser print dialog as soon as the tab loads — the dialog
  // doubles as the preview, so the cashier sees the receipt and can hit
  // Save/Print without an intermediate click. The on-screen buttons
  // below are a fallback for re-print without reloading.
  useEffect(() => {
    printReceipt()
  }, [])

  return (
    <>
      <Button
        type="button"
        size="sm"
        onClick={printReceipt}
        className="gap-1.5"
      >
        <Printer className="h-4 w-4" />
        Imprimir
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => window.close()}
        className="gap-1.5"
      >
        <X className="h-4 w-4" />
        Cerrar
      </Button>
    </>
  )
}
