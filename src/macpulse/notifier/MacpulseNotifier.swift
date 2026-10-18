// MacpulseNotifier: a tiny menu-less app that posts macpulse alerts.
//
// Post:   MacpulseNotifier --title T --message M [--status-file PATH] [--exec CMD ARGS...]
// Run:    MacpulseNotifier --run PROGRAM ARGS...   (used by the background agent)
// Click:  macOS relaunches the app and hands it the click; it runs the --exec
//         command saved with the notification (macpulse uses `macpulse report`).
//
// Built locally by `macpulse notifier install`, so it needs no Apple signature.

import AppKit
import UserNotifications

func parse(_ argv: [String]) -> (options: [String: String], exec: [String]) {
    var options: [String: String] = [:]
    var exec: [String] = []
    var i = 1
    while i < argv.count {
        let arg = argv[i]
        if arg == "--exec" {
            exec = Array(argv[(i + 1)...])
            break
        }
        if arg.hasPrefix("--"), i + 1 < argv.count {
            options[String(arg.dropFirst(2))] = argv[i + 1]
            i += 2
        } else {
            i += 1
        }
    }
    return (options, exec)
}

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    let args = parse(CommandLine.arguments)

    func applicationWillFinishLaunching(_ notification: Notification) {
        // Must be set before launch finishes, or a click that relaunched us is lost.
        UNUserNotificationCenter.current().delegate = self
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let message = args.options["message"] else {
            // Launched by macOS to deliver a click (or by hand): wait for it, then quit.
            quit(after: 10)
            return
        }
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            guard granted else {
                self.finish("denied: \(error?.localizedDescription ?? "notifications are turned off")")
                return
            }
            let content = UNMutableNotificationContent()
            content.title = self.args.options["title"] ?? "macpulse"
            content.body = message
            content.sound = .default
            content.threadIdentifier = "macpulse"
            if !self.args.exec.isEmpty {
                content.userInfo = ["exec": self.args.exec]
            }
            let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
            center.add(request) { error in
                self.finish(error.map { "error: \($0.localizedDescription)" } ?? "ok")
            }
        }
    }

    // Show the banner even if this app happens to be frontmost.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.actionIdentifier == UNNotificationDefaultActionIdentifier,
           let exec = response.notification.request.content.userInfo["exec"] as? [String], !exec.isEmpty {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: exec[0])
            process.arguments = Array(exec.dropFirst())
            try? process.run()
        }
        completionHandler()
        quit(after: 1)
    }

    func finish(_ status: String) {
        if let path = args.options["status-file"] {
            try? status.write(toFile: path, atomically: true, encoding: .utf8)
        }
        // Linger briefly so a quick click is handled by this process.
        quit(after: status == "ok" ? 3 : 0)
    }

    func quit(after seconds: Double) {
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { NSApp.terminate(nil) }
    }
}

// Run mode: launchd starts macpulse's background job through this app, so macOS
// lists it as "macpulse" in Login Items instead of "python3".
let argv = CommandLine.arguments
if argv.count >= 3 && argv[1] == "--run" {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: argv[2])
    process.arguments = Array(argv[3...])
    do {
        try process.run()
    } catch {
        FileHandle.standardError.write("macpulse: could not start \(argv[2]): \(error)\n".data(using: .utf8)!)
        exit(127)
    }
    process.waitUntilExit()
    exit(process.terminationStatus)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
