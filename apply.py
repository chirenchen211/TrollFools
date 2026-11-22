#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply.py — 给 TrollFools 源码自动打「注入目标可选」补丁

用法：
    python apply.py /path/to/TrollFools            # 打补丁
    python apply.py /path/to/TrollFools --dry-run  # 只看会改什么
    python apply.py /path/to/TrollFools --revert   # 还原

原理：anchor-based 精确替换。每个补丁都要求 anchor 在文件里**恰好出现一次**，
      否则整体中止，不会留下半套改动。原文件会备份为 *.orig。

改动清单：
  1. InjectorV3.swift          加 preferredMachOPath 属性
  2. InjectorV3+Inject.swift   locateAvailableMachO() 支持优先指定目标
  3. InjectorV3+Inject.swift   注入成功后把目标写进 InjectionTargetStore
  4. InjectedPlugIn.swift      加 targetMachO 字段
  5. PlugInCell.swift          加 "Framework >" 胶囊按钮
  6. CLI/CmdInject.swift       加 -t/--target
  7. 新增 InjectionTargetStore.swift
  8. 新增 TargetPickerSheet.swift
"""

import os
import shutil
import sys

# ---------------------------------------------------------------------------
# 补丁定义：(文件, anchor, replacement, 说明)
# ---------------------------------------------------------------------------

PATCHES = []


def P(path, old, new, note):
    PATCHES.append((path, old, new, note))


# ---- 1. InjectorV3.swift ---------------------------------------------------

P("TrollFools/InjectorV3.swift",
  """    var preferMainExecutable: Bool = false
""",
  """    var preferMainExecutable: Bool = false

    /// 用户手动指定的注入目标（相对 bundle 根的路径，如 "Frameworks/Foo.framework/Foo"）。
    /// 为 nil 或空串时，沿用原版的自动选择逻辑。
    var preferredMachOPath: String?

    /// 本次注入是否使用了用户指定的目标（用于结果页提示）。
    var didUseUserSelectedTarget: Bool = false
""",
  "InjectorV3: 新增 preferredMachOPath / didUseUserSelectedTarget")


# ---- 2+3. InjectorV3+Inject.swift -----------------------------------------

P("TrollFools/InjectorV3+Inject.swift",
  """    fileprivate func locateAvailableMachO() throws -> URL? {
        let allMachOs = try frameworkMachOsInBundle(bundleURL)

        DDLogInfo("Mach-O scan: \\(allMachOs.count) candidates in \\(bundleURL.lastPathComponent)", ddlog: logger)
""",
  """    fileprivate func locateAvailableMachO() throws -> URL? {
        let allMachOs = try frameworkMachOsInBundle(bundleURL)

        DDLogInfo("Mach-O scan: \\(allMachOs.count) candidates in \\(bundleURL.lastPathComponent)", ddlog: logger)

        // ---- 用户指定目标优先 ----
        if let preferredMachOPath, !preferredMachOPath.isEmpty {
            let absolute = bundleURL.appendingPathComponent(preferredMachOPath)
            let want = absolute.standardizedFileURL
            if let hit = allMachOs.first(where: { $0.standardizedFileURL == want }) {
                DDLogInfo("User-selected Mach-O: \\(hit.path)", ddlog: logger)
                didUseUserSelectedTarget = true
                return hit
            }
            DDLogError("Preferred Mach-O is not an available candidate: \\(preferredMachOPath)", ddlog: logger)
            throw Error.generic(NSLocalizedString(
                "The selected inject target is not available (encrypted or missing).",
                comment: ""))
        }
""",
  "InjectorV3+Inject: locateAvailableMachO 支持用户指定目标")

P("TrollFools/InjectorV3+Inject.swift",
  """        DDLogInfo("Best matched Mach-O is \\(targetMachO.path)", ddlog: logger)
""",
  """        DDLogInfo("Best matched Mach-O is \\(targetMachO.path)", ddlog: logger)

        // ---- 记录本次注入目标，供插件列表展示 / 改目标 ----
        let targetRelPath = targetMachO.path
            .replacingOccurrences(of: bundleURL.path + "/", with: "")
        for assetURL in assetURLs where assetURL.pathExtension.lowercased() != "bundle" {
            InjectionTargetStore.set(targetRelPath,
                                     for: assetURL.lastPathComponent,
                                     bid: appID)
        }
""",
  "InjectorV3+Inject: 注入后记录目标到 InjectionTargetStore")


# ---- 4. InjectedPlugIn.swift ----------------------------------------------

P("TrollFools/InjectedPlugIn.swift",
  """    let isEnabled: Bool

    init(url: URL, isEnabled: Bool) {
        self.id = url.absoluteString
        self.url = url
        self.createdAt = (try? url.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? Date()
        self.isEnabled = isEnabled
    }
""",
  """    let isEnabled: Bool
    /// 注入目标（相对 .app 根的路径）。nil 表示主程序或未记录。
    let targetMachO: String?

    init(url: URL, isEnabled: Bool, targetMachO: String? = nil) {
        self.id = url.absoluteString
        self.url = url
        self.createdAt = (try? url.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? Date()
        self.isEnabled = isEnabled
        self.targetMachO = targetMachO
    }

    /// 胶囊标签文案
    var targetKindLabel: String {
        guard let t = targetMachO, !t.isEmpty else { return "Main" }
        if t.contains(".framework/") { return "Framework" }
        if t.hasSuffix(".dylib") { return "Dylib" }
        if t.contains(".appex/") { return "Extension" }
        return "Mach-O"
    }
""",
  "InjectedPlugIn: 加 targetMachO 字段与 targetKindLabel")


# ---- 5. PlugInCell.swift ---------------------------------------------------

P("TrollFools/PlugInCell.swift",
  """                    Text(gDateFormatter.string(from: plugIn.createdAt))
                        .font(.subheadline)
                        .lineLimit(1)
""",
  """                    Text(gDateFormatter.string(from: plugIn.createdAt))
                        .font(.subheadline)
                        .lineLimit(1)

                    Button {
                        isTargetPickerPresented = true
                    } label: {
                        HStack(spacing: 3) {
                            Image(systemName: "shippingbox")
                                .font(.system(size: 10))
                            Text(plugIn.targetKindLabel)
                            Text(">")
                        }
                        .font(.caption2)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Color.secondary.opacity(0.12))
                        .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 2)
""",
  "PlugInCell: 加注入目标胶囊按钮")

P("TrollFools/PlugInCell.swift",
  """    @Binding var quickLookExport: URL?
    @State var isEnabled: Bool = false
""",
  """    @Binding var quickLookExport: URL?
    @State var isEnabled: Bool = false
    @State private var isTargetPickerPresented: Bool = false
""",
  "PlugInCell: 加 sheet 状态")

P("TrollFools/PlugInCell.swift",
  """        .contextMenu {
""",
  """        .sheet(isPresented: $isTargetPickerPresented) {
            TargetPickerSheet(
                plugIn: plugIn,
                bundleURL: ejectList.app.url,
                currentTarget: plugIn.targetMachO
            ) { newTarget in
                ejectList.changeTarget(of: plugIn, to: newTarget)
                isTargetPickerPresented = false
            }
        }
        .contextMenu {
""",
  "PlugInCell: 挂上目标选择 sheet")


# ---- 6. CLI -----------------------------------------------------------------

P("TrollFools/CLI/CmdInject.swift",
  """    @Flag(name: [.customLong("weak")], help: "Use weak reference.")
    var weakReference: Bool = false
""",
  """    @Flag(name: [.customLong("weak")], help: "Use weak reference.")
    var weakReference: Bool = false

    @Option(name: [.customLong("target"), .customShort("t")],
            help: "Injection target Mach-O, relative to the .app bundle (default: auto).")
    var targetMachOPath: String?
""",
  "CmdInject: 加 --target")

P("TrollFools/CLI/CmdInject.swift",
  """        injector.useWeakReference = weakReference
""",
  """        injector.useWeakReference = weakReference
        if let targetMachOPath {
            injector.preferredMachOPath = targetMachOPath
        }
""",
  "CmdInject: 传入 preferredMachOPath")


# ---- 7. EjectListModel.swift ----------------------------------------------

P("TrollFools/EjectListModel.swift",
  """        plugIns += InjectorV3.main.injectedAssetURLsInBundle(app.url)
            .map { InjectedPlugIn(url: $0, isEnabled: true) }
""",
  """        plugIns += InjectorV3.main.injectedAssetURLsInBundle(app.url)
            .map {
                InjectedPlugIn(
                    url: $0,
                    isEnabled: true,
                    targetMachO: InjectionTargetStore.target(of: $0.lastPathComponent, bid: app.bid)
                )
            }
""",
  "EjectListModel: 已注入插件带上目标信息")

P("TrollFools/EjectListModel.swift",
  """            .map { InjectedPlugIn(url: $0, isEnabled: false) }
""",
  """            .map {
                InjectedPlugIn(
                    url: $0,
                    isEnabled: false,
                    targetMachO: InjectionTargetStore.target(of: $0.lastPathComponent, bid: app.bid)
                )
            }
""",
  "EjectListModel: 已禁用插件带上目标信息")

P("TrollFools/EjectListModel.swift",
  """    func togglePlugIn(_ plugIn: InjectedPlugIn, isEnabled: Bool) {
        guard plugIn.isEnabled != isEnabled else {
            return
        }
        processingPlugIn = plugIn
    }
}
""",
  """    func togglePlugIn(_ plugIn: InjectedPlugIn, isEnabled: Bool) {
        guard plugIn.isEnabled != isEnabled else {
            return
        }
        processingPlugIn = plugIn
    }

    /// 把某个插件从当前目标摘下，再注入到新的 Mach-O 目标。
    /// 流程：persistIfNecessary 保证持久副本 -> eject 摘除旧 load command -> 按新目标 inject。
    func changeTarget(of plugIn: InjectedPlugIn, to newTarget: String) {
        processingPlugIn = plugIn

        let plugInName = plugIn.url.lastPathComponent
        let appURL = app.url
        let appBID = app.bid
        let currentURL = plugIn.url

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            do {
                let injector = try InjectorV3(appURL)

                // 1) 摘掉旧注入（shouldDesist: false 会把插件留一份到持久目录）
                try injector.eject([currentURL], shouldDesist: false)

                // 2) 从持久副本重新注入，避免源文件已被 eject 删除
                let persisted = InjectorV3.persistentPlugInsRootURL
                    .appendingPathComponent(appBID, isDirectory: true)
                    .appendingPathComponent(plugInName)
                let source = FileManager.default.fileExists(atPath: persisted.path)
                    ? persisted
                    : currentURL

                injector.preferredMachOPath = newTarget
                try injector.inject([source], shouldPersist: true)
                injector.preferredMachOPath = nil

                // 3) 记录新目标
                InjectionTargetStore.set(newTarget, for: plugInName, bid: appBID)

                DispatchQueue.main.async {
                    self?.processingPlugIn = nil
                    self?.reload()
                }
            } catch {
                NSLog("[TrollFools] changeTarget failed: %@", "\\(error)")
                DispatchQueue.main.async {
                    self?.processingPlugIn = nil
                }
            }
        }
    }
}
""",
  "EjectListModel: 新增 changeTarget（先 eject 再 inject）")


# ---------------------------------------------------------------------------
# 新增文件（整文件写入）
# ---------------------------------------------------------------------------

NEW_FILES = {}

NEW_FILES["TrollFools/InjectionTargetStore.swift"] = r'''//
//  InjectionTargetStore.swift
//  TrollFools
//
//  记录「插件文件名 -> 注入目标 Mach-O（相对 .app 路径）」。
//

import Foundation

enum InjectionTargetStore {
    static func fileURL(for bundleIdentifier: String) -> URL {
        InjectorV3.persistentPlugInsRootURL
            .appendingPathComponent(bundleIdentifier, isDirectory: true)
            .appendingPathComponent(".targets.json")
    }

    static func load(_ bid: String) -> [String: String] {
        guard let data = try? Data(contentsOf: fileURL(for: bid)),
              let map = try? JSONDecoder().decode([String: String].self, from: data)
        else { return [:] }
        return map
    }

    static func target(of plugInName: String, bid: String) -> String? {
        load(bid)[plugInName]
    }

    static func set(_ target: String, for plugInName: String, bid: String) {
        var map = load(bid)
        map[plugInName] = target
        write(map, bid: bid)
    }

    static func remove(_ plugInName: String, bid: String) {
        var map = load(bid)
        map.removeValue(forKey: plugInName)
        write(map, bid: bid)
    }

    private static func write(_ map: [String: String], bid: String) {
        let url = fileURL(for: bid)
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(map) {
            try? data.write(to: url, options: .atomic)
        }
    }
}
'''

NEW_FILES["TrollFools/TargetPickerSheet.swift"] = r'''//
//  TargetPickerSheet.swift
//  TrollFools
//
//  注入目标选择器：主程序 / Frameworks 内各可注入 Mach-O。
//

import SwiftUI

struct TargetPickerSheet: View {
    let plugIn: InjectedPlugIn
    let bundleURL: URL
    let currentTarget: String?
    let onPick: (String) -> Void

    @Environment(\.presentationMode) private var presentationMode

    private struct Candidate: Identifiable {
        let id = UUID()
        let label: String
        let relativePath: String
        let isMain: Bool
    }

    private var candidates: [Candidate] {
        var out: [Candidate] = []

        let mainName = bundleURL.deletingPathExtension().lastPathComponent
        out.append(Candidate(label: mainName, relativePath: mainName, isMain: true))

        let frameworks = bundleURL.appendingPathComponent("Frameworks")
        if let en = FileManager.default.enumerator(
            at: frameworks,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) {
            while let u = en.nextObject() as? URL {
                guard let v = try? u.resourceValues(forKeys: [.isRegularFileKey]),
                      v.isRegularFile == true else { continue }
                let ext = u.pathExtension.lowercased()
                guard ext.isEmpty || ext == "dylib" else { continue }
                let rel = u.path.replacingOccurrences(of: bundleURL.path + "/", with: "")
                out.append(Candidate(label: rel, relativePath: rel, isMain: false))
            }
        }
        return out
    }

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("当前注入目标").font(.footnote)) {
                    Text(currentTarget ?? "自动")
                        .font(.callout.monospaced())
                        .foregroundColor(.secondary)
                }

                Section(header: Text("可选项").font(.footnote)) {
                    ForEach(candidates) { c in
                        Button {
                            onPick(c.relativePath)
                            presentationMode.wrappedValue.dismiss()
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(c.isMain ? "主程序" : "Framework / Dylib")
                                        .font(.caption2)
                                        .foregroundColor(.secondary)
                                    Text(c.label)
                                        .font(.callout)
                                        .lineLimit(2)
                                }
                                Spacer()
                                if currentTarget == c.relativePath {
                                    Image(systemName: "checkmark")
                                        .foregroundColor(.accentColor)
                                }
                            }
                        }
                    }
                }

                Section {
                    Text("改目标会重新写入 load command，App 需要重新启动。")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
            .navigationTitle(plugIn.url.lastPathComponent)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(NSLocalizedString("Cancel", comment: "")) { presentationMode.wrappedValue.dismiss() }
                }
            }
        }
    }
}
'''


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def find_repo(arg):
    p = os.path.abspath(arg)
    for cand in (p, os.path.join(p, "TrollFools")):
        if os.path.isfile(os.path.join(cand, "TrollFools", "InjectorV3.swift")):
            return cand
    if os.path.isfile(os.path.join(p, "InjectorV3.swift")):
        return os.path.dirname(p)
    return None


def apply(root, dry_run=False):
    ok = 0
    fails = []

    for rel, old, new, note in PATCHES:
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            fails.append((rel, note, "文件不存在"))
            continue
        with open(full, "r", encoding="utf-8") as f:
            src = f.read()

        if new.strip() in src:
            print("  [skip] %-40s %s（已打过）" % (rel, note))
            ok += 1
            continue

        n = src.count(old)
        if n != 1:
            fails.append((rel, note, "anchor 命中 %d 次（需要恰好 1 次）" % n))
            continue

        if dry_run:
            print("  [would] %-40s %s" % (rel, note))
            ok += 1
            continue

        if not os.path.exists(full + ".orig"):
            shutil.copy2(full, full + ".orig")
        with open(full, "w", encoding="utf-8") as f:
            f.write(src.replace(old, new, 1))
        print("  [ok]   %-40s %s" % (rel, note))
        ok += 1

    for rel, content in NEW_FILES.items():
        full = os.path.join(root, rel)
        if dry_run:
            print("  [would] %-40s 新增文件" % rel)
            ok += 1
            continue
        if os.path.exists(full):
            print("  [skip] %-40s 已存在" % rel)
        else:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            print("  [new]  %-40s 新增文件" % rel)
        ok += 1

    return ok, fails


def revert(root):
    n = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".orig"):
                src = os.path.join(dirpath, fn)
                dst = src[:-5]
                shutil.move(src, dst)
                print("  [revert] %s" % os.path.relpath(dst, root))
                n += 1
    for rel in NEW_FILES:
        full = os.path.join(root, rel)
        if os.path.isfile(full) and not os.path.exists(full + ".orig"):
            os.remove(full)
            print("  [remove] %s" % rel)
            n += 1
    return n


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    root = find_repo(sys.argv[1])
    if not root:
        print("找不到 TrollFools 源码根目录（应含 TrollFools/InjectorV3.swift）：%s" % sys.argv[1])
        sys.exit(1)

    dry = "--dry-run" in sys.argv
    rev = "--revert" in sys.argv

    print("TrollFools 源码根：%s" % root)
    if rev:
        print("\n还原中：")
        n = revert(root)
        print("\n完成，还原 %d 处。" % n)
        return

    print("\n%s：" % ("预演" if dry else "打补丁"))
    ok, fails = apply(root, dry_run=dry)

    print("\n成功 %d 项，失败 %d 项。" % (ok, len(fails)))
    if fails:
        print("\n以下补丁未应用（整体未完整，请检查上游是否已变更）：")
        for rel, note, why in fails:
            print("  ✗ %-40s %s —— %s" % (rel, note, why))
        print("\n提示：先 `--revert` 回滚，再对照 README 手工确认 anchor。")
        sys.exit(2)

    print("""
下一步（macOS + Xcode）：
  cd %s
  make            # 出 packages/*.deb
  make DEBUG=1    # 调试版

新能力：
  · 插件列表里每个 dylib 多了 "Framework >" 胶囊，点击可换注入目标
  · CLI: trollfoolscli inject <bundleId> -p libhook.dylib -t Frameworks/Foo.framework/Foo
""" % root)


if __name__ == "__main__":
    main()
