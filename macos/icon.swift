// Icône d'arXivReader (1024 × 1024 px) : carré arrondi rouge arXiv, page blanche et χ.
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

// page, coin replié
let page = NSBezierPath()
page.move(to: NSPoint(x: 300, y: 230))
page.line(to: NSPoint(x: 724, y: 230))
page.line(to: NSPoint(x: 724, y: 690))
page.line(to: NSPoint(x: 624, y: 790))
page.line(to: NSPoint(x: 300, y: 790))
page.close()
NSColor(white: 1, alpha: 0.97).setFill()
page.fill()
let fold = NSBezierPath()
fold.move(to: NSPoint(x: 624, y: 790))
fold.line(to: NSPoint(x: 624, y: 690))
fold.line(to: NSPoint(x: 724, y: 690))
fold.close()
NSColor(white: 0.82, alpha: 1).setFill()
fold.fill()

// χ d'arXiv, et lignes de texte
let font = NSFont(name: "Georgia-Italic", size: 360) ?? NSFont.systemFont(ofSize: 360)
let chi = NSAttributedString(string: "χ", attributes: [.font: font, .foregroundColor: NSColor(srgbRed: 0.70, green: 0.11, blue: 0.11, alpha: 1)])
let box = chi.size()
chi.draw(at: NSPoint(x: 512 - box.width / 2, y: 470 - box.height / 2 + 30))
NSColor(white: 0.72, alpha: 1).setFill()
for (i, width) in [300.0, 340.0, 250.0].enumerated() {
    NSBezierPath(roundedRect: NSRect(x: 342, y: 330 - Double(i) * 46, width: width, height: 18), xRadius: 9, yRadius: 9).fill()
}

image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try! bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
