import SwiftUI

enum CodexPalette {
    static let sidebar = Color.dynamic(light: 0xF7F7F7, dark: 0x222222)
    static let canvas = Color.dynamic(light: 0xFFFFFF, dark: 0x181818)
    static let surface = Color.dynamic(light: 0xF5F5F5, dark: 0x252525)
    static let raised = Color.dynamic(light: 0xFFFFFF, dark: 0x202020)
    static let ink = Color.dynamic(light: 0x202020, dark: 0xEEEEEE)
    static let secondaryInk = Color.dynamic(light: 0x737373, dark: 0xA3A3A3)
    static let line = Color.dynamic(light: 0xE5E5E5, dark: 0x383838)
    static let cobalt = Color.dynamic(light: 0x202020, dark: 0xEEEEEE)
    static let teal = Color.dynamic(light: 0x287D78, dark: 0x62C8BE)
    static let amber = Color.dynamic(light: 0xB76A22, dark: 0xF0B266)
    static let danger = Color.dynamic(light: 0xB83F4A, dark: 0xFF8992)
}

private extension Color {
    static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            UIColor(rgb: traits.userInterfaceStyle == .dark ? dark : light)
        })
    }
}

private extension UIColor {
    convenience init(rgb: UInt32) {
        self.init(
            red: CGFloat((rgb >> 16) & 0xFF) / 255,
            green: CGFloat((rgb >> 8) & 0xFF) / 255,
            blue: CGFloat(rgb & 0xFF) / 255,
            alpha: 1
        )
    }
}

struct CodexPanelModifier: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var padding: CGFloat = 16

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(reduceTransparency ? CodexPalette.raised : CodexPalette.surface.opacity(0.92))
            }
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(CodexPalette.line.opacity(0.72), lineWidth: 0.5)
            }
    }
}

extension View {
    func codexPanel(padding: CGFloat = 16) -> some View {
        modifier(CodexPanelModifier(padding: padding))
    }

    func codexDisplayTitle() -> some View {
        font(.system(.title2, design: .default, weight: .semibold))
            .foregroundStyle(CodexPalette.ink)
    }
}
