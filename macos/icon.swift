// Icône d'arXivReader (1024 × 1024 px) : carré arrondi rouge arXiv et χ blanc.
// swift macos/icon.swift sortie.png

import AppKit

let size = 1024.0
let output = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()

// fond : grille des icônes macOS (carré de 824 px, marge de 100 px)
let tile = NSRect(x: 100, y: 100, width: 824, height: 824)
let shape = NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185)
NSGraphicsContext.saveGraphicsState()
let shadow = NSShadow()
shadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
shadow.shadowBlurRadius = 24
shadow.shadowOffset = NSSize(width: 0, height: -10)
shadow.set()
NSColor(srgbRed: 0.70, green: 0.11, blue: 0.11, alpha: 1).setFill()
shape.fill()
NSGraphicsContext.restoreGraphicsState()
NSGradient(starting: NSColor(srgbRed: 0.80, green: 0.16, blue: 0.15, alpha: 1),
           ending: NSColor(srgbRed: 0.55, green: 0.07, blue: 0.08, alpha: 1))!.draw(in: shape, angle: -90)

// χ blanc, centré d'après le dessin de la lettre (pas d'après la ligne de texte)
let font = NSFont(name: "Georgia-Italic", size: 632) ?? NSFont.systemFont(ofSize: 632)
let line = CTLineCreateWithAttributedString(NSAttributedString(string: "χ", attributes: [.font: font, .foregroundColor: NSColor.white]))
let bounds = CTLineGetBoundsWithOptions(line, .useGlyphPathBounds)
let context = NSGraphicsContext.current!.cgContext
context.textPosition = CGPoint(x: tile.midX - bounds.midX, y: tile.midY - bounds.midY)
CTLineDraw(line, context)

image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try! bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
