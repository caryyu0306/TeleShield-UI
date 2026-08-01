import AppKit
import SwiftUI

enum TeleShieldDesign {
    static let pagePadding: CGFloat = 28
    static let contentMaxWidth: CGFloat = 1040
    static let cardRadius: CGFloat = 16
    static let innerRadius: CGFloat = 12
    static let controlRadius: CGFloat = 10

    static let success = Color(nsColor: .systemGreen)
    static let warning = Color(nsColor: .systemOrange)
    static let danger = Color(nsColor: .systemRed)
    static let muted = Color.secondary.opacity(0.82)
}

private struct TeleShieldSurfaceModifier: ViewModifier {
    let radius: CGFloat
    let fill: Color

    func body(content: Content) -> some View {
        content
            .background(fill, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
            }
    }
}

extension View {
    func teleShieldSurface(
        radius: CGFloat = TeleShieldDesign.cardRadius,
        fill: Color = Color(nsColor: .controlBackgroundColor)
    ) -> some View {
        modifier(TeleShieldSurfaceModifier(radius: radius, fill: fill))
    }

    func teleShieldPageContent() -> some View {
        frame(maxWidth: TeleShieldDesign.contentMaxWidth, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
    }
}

struct TeleShieldEmptyState: View {
    let icon: String
    let title: String
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: icon)
                .font(.headline)
            Text(message)
                .font(.callout)
                .foregroundStyle(TeleShieldDesign.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .teleShieldSurface(radius: TeleShieldDesign.cardRadius)
        .accessibilityElement(children: .combine)
    }
}
