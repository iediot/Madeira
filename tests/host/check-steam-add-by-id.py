#!/usr/bin/env python3
"""Compile production App ID parsing and CM free-license wire messages on the host.
No network, account, app build, or Steam credentials required.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
STEAM = ROOT / 'app/Madeira/SwiftSteam'
swiftc = os.environ.get('SWIFTC') or shutil.which('swiftc')
assert swiftc, 'swiftc is required'
checks = r'''
import Foundation

enum SteamLog { static func trace(_ s: String) {} ; static func event(_ s: String) {} }
enum SteamFileError: Error { case invalid(String) }
@MainActor final class FakeSession: SteamCMSession {
    var cellID: UInt32 = 0
    var steamID: UInt64 = 1
    var licenses: [UInt32] = []
    var appType = "Game"
    var freeResult: UInt32 = 1
    func ensureConnected() async throws {}
    func awaitLicenseList(timeout: TimeInterval) async throws -> [UInt32] { licenses }
    func incoming(_ msg: EMsg, _ body: Data) -> SteamMessageCodec.IncomingMessage {
        .init(eMsg: msg, rawEMsg: msg.rawValue, isProtobuf: true, header: CMsgProtoBufHeader(), body: body)
    }
    func sendAndWait(eMsg: EMsg, body: Data, responseEMsg: EMsg, timeout: TimeInterval) async throws -> SteamMessageCodec.IncomingMessage {
        if eMsg == .clientPICSAccessTokenRequest { return incoming(responseEMsg, Data()) }
        check(eMsg == .clientRequestFreeLicense && responseEMsg == .clientRequestFreeLicenseResponse,
              "free-license sendAndWait message pairing")
        check(body == Data([0x10, 0xb8, 3]), "fetcher sends app 440 using field two")
        var result = ProtobufEncoder()
        result.writeUInt32(fieldNumber: 1, value: freeResult)
        if freeResult == 1 { result.writeUInt32(fieldNumber: 2, value: 42) }
        return incoming(responseEMsg, result.data)
    }
    func sendAndWaitPICS(eMsg: EMsg, body: Data, timeout: TimeInterval) async throws -> [SteamMessageCodec.IncomingMessage] {
        var request = ProtobufDecoder(body), response = ProtobufEncoder()
        while let tag = try request.readTag() {
            let package = tag.fieldNumber == 1
            var entry = ProtobufDecoder(try request.readBytes())
            _ = try entry.readTag()
            let id = UInt32(try entry.readVarint())
            var info = ProtobufEncoder()
            info.writeUInt32(fieldNumber: 1, value: id)
            if package {
                check(id == 42, "refresh resolves granted package")
                var buffer = Data([0]) + Data("appids".utf8) + Data([0, 2, 48, 0])
                buffer.append(contentsOf: [0xb8, 1, 0, 0, 8]) // 440 little endian
                info.writeBytes(fieldNumber: 5, value: buffer)
            } else {
                let vdf = "\"common\" { \"name\" \"Fixture\" \"type\" \"\(appType)\" \"oslist\" \"windows\" \"freetogame\" \"1\" } \"depots\" { \"441\" { \"manifests\" { \"public\" \"123\" } } }"
                info.writeBytes(fieldNumber: 5, value: Data(vdf.utf8))
            }
            response.writeSubmessage(fieldNumber: package ? 3 : 1, value: info.data)
        }
        return [incoming(.clientPICSProductInfoResponse, response.data)]
    }
    func callServiceMethod(method: SteamServiceMethod, body: Data, timeout: TimeInterval) async throws -> Data {
        fatalError("unexpected service call")
    }
}

func check(_ condition: Bool, _ name: String) {
    guard condition else { fatalError("FAIL: " + name) }
    print("PASS: " + name)
}
@main struct Checks {
    @MainActor static func main() async throws {
        for text in ["440", " 440\n", "000440", "https://store.steampowered.com/app/440/Team_Fortress_2/",
                     "store.steampowered.com/app/440", "https://store.steampowered.com/app/440/?l=english#x",
                     "steam://run/440", "steam://install/440/", "s.team/a/440", "https://s.team/a/440",
                     "HTTPS://STORE.STEAMPOWERED.COM/app/440"] {
            check(SteamAppInput.parse(text) == 440, "parse " + text)
        }
        for text in ["", "0", "-440", "+440", "4.40", "４４０", "4294967296", "steam://run/0",
                     "https://evil.test/app/440", "https://store.steampowered.com.evil.test/app/440",
                     "https://store.steampowered.com/sub/440", "https://store.steampowered.com/bundle/440",
                     "https://evil@store.steampowered.com/app/440", "https://store.steampowered.com:443/app/440",
                     "steam://run/440/other", "steam://purchase/440", "s.team/a/nope", "s.team/a/440/other",
                     "file://store.steampowered.com/app/440", "440 trailing"] {
            check(SteamAppInput.parse(text) == nil, "reject " + text)
        }
        check(SteamAppInput.parse("4294967295") == UInt32.max, "UInt32 boundary")
        // Valve steammessages_clientserver_2.proto: appids is field TWO.
        check(CMsgClientRequestFreeLicense(appids: [440]).serialize() == Data([0x10, 0xb8, 0x03]), "TF2 request wire bytes")
        check(CMsgClientRequestFreeLicense(appids: [440, 570]).serialize() == Data([0x10, 0xb8, 0x03, 0x10, 0xba, 0x04]), "repeated request IDs")
        check(CMsgClientRequestFreeLicense().serialize().isEmpty, "empty request")
        check(EMsg.clientRequestFreeLicense.rawValue == 5572 && EMsg.clientRequestFreeLicenseResponse.rawValue == 5573, "CM message numbers")
        let normal = try CMsgClientRequestFreeLicenseResponse.deserialize(from: Data([8, 1, 16, 42, 16, 43, 24, 0xb8, 3]))
        check(normal.eresult == 1 && normal.grantedPackageids == [42, 43] && normal.grantedAppids == [440], "response fields")
        let packed = try CMsgClientRequestFreeLicenseResponse.deserialize(from: Data([8, 1, 18, 2, 42, 43, 26, 2, 0xb8, 3, 40, 7]))
        check(packed.grantedPackageids == [42, 43] && packed.grantedAppids == [440], "packed fields and unknown field")
        let empty = try CMsgClientRequestFreeLicenseResponse.deserialize(from: Data())
        check(empty.eresult == 2 && empty.grantedAppids.isEmpty && empty.grantedPackageids.isEmpty, "missing result defaults to failure")
        for bytes: [UInt8] in [[18, 3, 1], [24, 0x80], [26, 1, 0x80]] {
            do {
                _ = try CMsgClientRequestFreeLicenseResponse.deserialize(from: Data(bytes))
                fatalError("accepted malformed protobuf")
            } catch { print("PASS: malformed response rejected") }
        }
        let session = FakeSession()
        let fetcher = SteamLibraryFetcher(session: session)
        let before = try await fetcher.owns(appID: 440)
        check(!before, "public PICS metadata does not establish ownership")
        let info = try await fetcher.fetchAppInfo(appID: 440)
        check(info?.name == "Fixture" && info?.freeToPlay == true && info?.hasWindowsDownload == true,
              "resolve name, free flag and Windows depots via PICS")
        let grant = try await fetcher.requestFreeLicense(appID: 440)
        let apps = try await fetcher.fetchOwnedApps(including: [440], grantedPackages: grant.grantedPackageids,
                                                  grantedApps: grant.grantedAppids)
        check(apps.map(\.appID) == [440], "package-only grant resolves before license push")
        session.licenses = [42]
        let owned = try await fetcher.owns(appID: 440)
        check(owned, "ownership after license push")
        session.appType = "Tool"
        let defaultApps = try await fetcher.fetchOwnedApps()
        let manual = try await fetcher.fetchOwnedApps(including: [440])
        check(defaultApps.isEmpty && manual.map(\.appID) == [440], "manual ID survives default type filter")
        session.licenses = []
        let unowned = try await fetcher.fetchOwnedApps(including: [440])
        check(unowned.isEmpty, "manual ID alone cannot create ownership")
        let direct = try await fetcher.fetchOwnedApps(including: [440], grantedApps: [440])
        check(direct.map(\.appID) == [440], "app-only grant resolves through PICS")
        session.freeResult = 15
        let denied = try await fetcher.requestFreeLicense(appID: 440)
        check(denied.eresult == 15 && denied.grantedAppids.isEmpty && denied.grantedPackageids.isEmpty,
              "paid or unavailable app returns denial without grants")

    }
}
'''
with tempfile.TemporaryDirectory(prefix='steam-add-by-id-') as folder:
    work = Path(folder)
    (work / 'checks.swift').write_text(checks)
    exe = work / 'checks'
    production = [STEAM / 'Proto/SteamProtoMessages.swift', STEAM / 'Core/SteamError.swift', STEAM / 'Core/SteamProtocol.swift', STEAM / 'Core/SteamCMSession.swift',
                  STEAM / 'Core/SteamMessageCodec.swift', STEAM / 'Library/SteamAppInfo.swift',
                  STEAM / 'Library/SteamLibraryFetcher.swift']
    subprocess.run([swiftc, '-parse-as-library', '-swift-version', '5', '-module-cache-path', str(work / 'cache'),
                    *map(str, production), str(work / 'checks.swift'), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
