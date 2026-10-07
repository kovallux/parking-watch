import Foundation
import Security

guard CommandLine.arguments.count == 3 else { exit(2) }
let data = FileHandle.standardInput.readDataToEndOfFile()
guard !data.isEmpty else { exit(2) }
let query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: CommandLine.arguments[1],
    kSecAttrAccount as String: CommandLine.arguments[2]
]
var item = query
item[kSecValueData as String] = data
var status = SecItemAdd(item as CFDictionary, nil)
if status == errSecDuplicateItem {
    status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
}
exit(status == errSecSuccess ? 0 : 2)
