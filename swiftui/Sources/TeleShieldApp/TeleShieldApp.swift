import SwiftUI

@main
struct TeleShieldApp: App {
    @StateObject private var client = CoreClient()

    var body: some Scene {
        WindowGroup {
            ContentView(client: client)
        }

        MenuBarExtra("TeleShield", systemImage: "shield.lefthalf.filled") {
            MenuBarView(client: client)
        }
    }
}
