#!/usr/bin/env python3
"""Interactively compress PDFs and images with macOS frameworks."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Supported static raster formats handled by macOS ImageKit/AppKit.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".heic", ".heif"}

# Python handles prompts and paths; PDFKit/AppKit perform one native compression pass.
SWIFT_HELPER = r'''
import AppKit
import CoreGraphics
import Foundation
import PDFKit

// 【AI生成】
func compressPDF(inputPath: String, outputBase: String, maxPixels: CGFloat, quality: CGFloat) throws -> String {
    // Source URL identifies the original PDF without changing its path.
    let inputURL = URL(fileURLWithPath: inputPath)
    // Destination URL adds the native helper's PDF extension to the reserved output base.
    let outputURL = URL(fileURLWithPath: outputBase).appendingPathExtension("pdf")
    guard let document = PDFDocument(url: inputURL), document.pageCount > 0 else {
        throw NSError(domain: "PDFCompression", code: 1, userInfo: [NSLocalizedDescriptionKey: "无法读取 PDF"])
    }
    guard !document.isLocked else {
        throw NSError(domain: "PDFCompression", code: 5, userInfo: [NSLocalizedDescriptionKey: "PDF 已加密；请先解除密码保护"])
    }
    // Initial media box is replaced with each page's original crop box.
    var initialBox = CGRect.zero
    // CGContext writes the rasterized pages as a new PDF document.
    guard let context = CGContext(outputURL as CFURL, mediaBox: &initialBox, nil) else {
        throw NSError(domain: "PDFCompression", code: 2, userInfo: [NSLocalizedDescriptionKey: "无法创建压缩文件"])
    }
    for pageIndex in 0..<document.pageCount {
        // Current page index preserves the source document's page order.
        guard let page = document.page(at: pageIndex) else {
            throw NSError(domain: "PDFCompression", code: 3, userInfo: [NSLocalizedDescriptionKey: "无法读取 PDF 页面"])
        }
        // Crop-box bounds preserve the visible page size and orientation.
        let bounds = page.bounds(for: .cropBox)
        // Longest page dimension controls a bounded pixel rendering size.
        let safeDimension = max(bounds.width, bounds.height)
        // Scale never enlarges a page and follows the selected pixel profile.
        let scale = min(maxPixels / max(safeDimension, 1), 1)
        // Thumbnail size determines the maximum raster resolution.
        let pixelSize = CGSize(width: bounds.width * scale, height: bounds.height * scale)
        // Thumbnail renders the original page content before JPEG encoding.
        let thumbnail = page.thumbnail(of: pixelSize, for: .cropBox)
        // CGImage is the raster page handed to the JPEG encoder.
        guard let cgImage = thumbnail.cgImage(forProposedRect: nil, context: nil, hints: nil),
              // JPEG data applies lossy compression to bound output size.
              let jpegData = NSBitmapImageRep(cgImage: cgImage).representation(
                using: .jpeg,
                properties: [.compressionFactor: quality]
              ),
              // Decoded JPEG image is drawn into the destination PDF page.
              let jpegImage = NSBitmapImageRep(data: jpegData)?.cgImage else {
            throw NSError(domain: "PDFCompression", code: 4, userInfo: [NSLocalizedDescriptionKey: "无法压缩 PDF 页面"])
        }
        // Each output page retains its corresponding source page dimensions.
        var pageBox = bounds
        context.beginPage(mediaBox: &pageBox)
        context.draw(jpegImage, in: bounds)
        context.endPage()
    }
    context.closePDF()
    return outputURL.path
}

// 【AI生成】
func compressImage(inputPath: String, outputBase: String, maxPixels: CGFloat, quality: CGFloat) throws -> String {
    // NSImage decodes formats supported by the installed macOS image codecs.
    guard let image = NSImage(contentsOfFile: inputPath) else {
        throw NSError(domain: "ImageCompression", code: 1, userInfo: [NSLocalizedDescriptionKey: "无法读取图片"])
    }
    // Proposed rectangle lets AppKit select a decoded representation for this image.
    var proposedRect = CGRect(origin: .zero, size: image.size)
    guard let sourceImage = image.cgImage(forProposedRect: &proposedRect, context: nil, hints: nil) else {
        throw NSError(domain: "ImageCompression", code: 2, userInfo: [NSLocalizedDescriptionKey: "无法解码图片"])
    }
    // Longest dimension caps output resolution without enlarging smaller images.
    let longestSide = CGFloat(max(sourceImage.width, sourceImage.height))
    let scale = min(maxPixels / max(longestSide, 1), 1)
    let width = max(1, Int((CGFloat(sourceImage.width) * scale).rounded()))
    let height = max(1, Int((CGFloat(sourceImage.height) * scale).rounded()))
    // Alpha presence determines whether transparency must be retained in PNG output.
    let alphaInfo = sourceImage.alphaInfo
    let preservesAlpha = [
        CGImageAlphaInfo.first,
        .last,
        .premultipliedFirst,
        .premultipliedLast,
        .alphaOnly
    ].contains(alphaInfo)
    // Premultiplied alpha keeps transparent edges clean when resizing.
    let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
    guard let bitmap = CGContext(
        data: nil,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: bitmapInfo.rawValue
    ) else {
        throw NSError(domain: "ImageCompression", code: 3, userInfo: [NSLocalizedDescriptionKey: "无法创建图片位图"])
    }
    bitmap.interpolationQuality = .high
    bitmap.draw(sourceImage, in: CGRect(x: 0, y: 0, width: width, height: height))
    guard let resizedImage = bitmap.makeImage() else {
        throw NSError(domain: "ImageCompression", code: 4, userInfo: [NSLocalizedDescriptionKey: "无法缩放图片"])
    }
    // Transparent images remain PNG; opaque images use lossy JPEG for stronger compression.
    let fileType: NSBitmapImageRep.FileType = preservesAlpha ? .png : .jpeg
    let fileExtension = preservesAlpha ? "png" : "jpg"
    guard let encoded = NSBitmapImageRep(cgImage: resizedImage).representation(
        using: fileType,
        properties: preservesAlpha ? [:] : [.compressionFactor: quality]
    ) else {
        throw NSError(domain: "ImageCompression", code: 5, userInfo: [NSLocalizedDescriptionKey: "无法编码图片"])
    }
    // The chosen extension follows the actual output encoding, not the source suffix.
    let outputURL = URL(fileURLWithPath: outputBase).appendingPathExtension(fileExtension)
    try encoded.write(to: outputURL, options: .atomic)
    return outputURL.path
}

// 【AI生成】
func main() {
    // Arguments carry mode, source, output base, pixel cap, and JPEG quality.
    let arguments = Array(CommandLine.arguments.dropFirst())
    // Numeric settings control one-pass PDF or image rendering.
    guard arguments.count == 5,
          let maxPixels = Double(arguments[3]),
          let quality = Double(arguments[4]) else {
        fputs("参数错误\n", stderr)
        exit(2)
    }
    do {
        // Mode selects the matching native framework processing path.
        let resultPath: String
        if arguments[0] == "pdf" {
            resultPath = try compressPDF(
                inputPath: arguments[1],
                outputBase: arguments[2],
                maxPixels: CGFloat(maxPixels),
                quality: CGFloat(quality)
            )
        } else if arguments[0] == "image" {
            resultPath = try compressImage(
                inputPath: arguments[1],
                outputBase: arguments[2],
                maxPixels: CGFloat(maxPixels),
                quality: CGFloat(quality)
            )
        } else {
            throw NSError(domain: "Compression", code: 1, userInfo: [NSLocalizedDescriptionKey: "不支持的文件类型"])
        }
        print(resultPath)
    } catch {
        fputs("\(error.localizedDescription)\n", stderr)
        exit(1)
    }
}

main()
'''


# 【AI生成】
def ask_input_path():
    """Prompt for a supported file or folder and resolve shell-quoted paths."""
    # User-supplied path can be pasted with surrounding shell quotes.
    raw_path = input("请输入要压缩的 PDF/图片文件或文件夹路径：").strip()
    if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] and raw_path[0] in {"'", '"'}:
        raw_path = raw_path[1:-1]
    return Path(raw_path).expanduser().resolve()


# 【AI生成】
def ask_target_size():
    """Prompt repeatedly until the user enters a positive total size in MB."""
    while True:
        # Target size is interpreted as megabytes as requested.
        raw_size = input("请输入压缩后总大小上限（MB，只输入数字）：").strip()
        try:
            # Convert the positive megabyte value into bytes for comparison.
            target = float(raw_size)
            if target > 0:
                return int(target * 1024**2)
        except ValueError:
            pass
        print("请输入大于 0 的数字，例如 15。", file=sys.stderr)


# 【AI生成】
def collect_files(source):
    """Collect supported PDFs and static images from one file or folder."""
    supported = IMAGE_EXTENSIONS | {".pdf"}
    if source.is_file():
        return [source] if source.suffix.lower() in supported else []
    # A directory input is searched recursively for supported media files only.
    return sorted(path for path in source.rglob("*") if path.is_file() and path.suffix.lower() in supported)


# 【AI生成】
def create_output_dir(source):
    """Create a unique sibling folder without overwriting earlier output."""
    # Folder name is derived from the source and kept beside it.
    base_name = f"{source.stem if source.is_file() else source.name}_compressed"
    # Existing result folders are preserved by choosing the next free suffix.
    output = source.parent / base_name
    suffix = 2
    while output.exists():
        output = source.parent / f"{base_name}_{suffix}"
        suffix += 1
    output.mkdir(parents=True)
    return output


# 【AI生成】
def compression_profile(source_bytes, target_bytes):
    """Choose one page-resolution and JPEG-quality profile from the size ratio."""
    # Source-to-target ratio selects one profile to avoid repeated compression passes.
    ratio = min(target_bytes / max(source_bytes, 1), 1.0)
    if ratio >= 0.80:
        return 2400, 0.82
    if ratio >= 0.60:
        return 2200, 0.78
    if ratio >= 0.40:
        return 2000, 0.74
    if ratio >= 0.25:
        return 1800, 0.70
    if ratio >= 0.15:
        return 1600, 0.66
    if ratio >= 0.08:
        return 1400, 0.60
    if ratio >= 0.04:
        return 1200, 0.54
    return 1000, 0.48


# 【AI生成】
def compile_native_helper(directory):
    """Compile the embedded PDFKit helper once for this compression run."""
    # Swift compiler is provided by Xcode Command Line Tools on macOS.
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise RuntimeError("找不到 swiftc；请安装 macOS Xcode Command Line Tools")
    # Temporary source and executable are removed after this run completes.
    source = directory / "compress_pdf.swift"
    executable = directory / "compress_pdf"
    source.write_text(SWIFT_HELPER, encoding="utf-8")
    subprocess.run(
        [swiftc, str(source), "-o", str(executable)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return executable


# 【AI生成】
def output_path(output, relative, reserved):
    """Return a unique extensionless output base with the _compress suffix."""
    # Native code chooses the final extension according to PDF or detected image alpha.
    base = output / relative.parent / f"{relative.stem}_compress"
    # Reserve the extensionless base so same-named source files cannot collide.
    candidate = base
    index = 2
    while candidate in reserved:
        candidate = base.with_name(f"{base.name}_{index}")
        index += 1
    reserved.add(candidate)
    return candidate


# 【AI生成】
def main():
    """Collect interactive options and compress each media file once with macOS tools."""
    # CLI arguments are optional so running the script alone starts the prompts.
    parser = argparse.ArgumentParser(
        description="快速压缩 PDF 和静态图片：使用 macOS 自带框架处理。"
    )
    parser.add_argument("input", nargs="?", type=Path, help="PDF/图片文件或文件夹；省略时交互输入")
    parser.add_argument("--max-total-size", type=float, help="压缩后总大小目标，单位 MB")
    # Parsed options provide either interactive or command-line input.
    args = parser.parse_args()

    # Resolve the supplied input or request it interactively.
    source = args.input.expanduser().resolve() if args.input else ask_input_path()
    if not source.exists() or not (source.is_file() or source.is_dir()):
        parser.error(f"路径不存在或不是文件/文件夹：{source}")
    # A numeric target is already MB; otherwise ask for the interactive value.
    target_bytes = (
        int(args.max_total_size * 1024**2)
        if args.max_total_size and args.max_total_size > 0
        else ask_target_size()
    )
    # Filter once so prior compression output does not affect this run.
    files = collect_files(source)
    if not files:
        parser.error("没有找到支持的 PDF 或图片文件")

    # Aggregate source size chooses a single rendering profile for the batch.
    source_bytes = sum(path.stat().st_size for path in files)
    # One shared profile keeps the batch fast and consistent.
    max_pixels, quality = compression_profile(source_bytes, target_bytes)
    # Track the total and any per-file errors for the final status.
    source_root = source if source.is_dir() else source.parent
    compressed_total = 0
    failures = 0
    reserved_outputs = set()

    # Compile the system-framework helper once; invoke it once per source file.
    with tempfile.TemporaryDirectory(prefix="compress_media_") as temp_name:
        try:
            # Temporary compiler output does not require a Python package install.
            helper = compile_native_helper(Path(temp_name))
        except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
            parser.error(f"无法准备 macOS PDF 压缩工具：{error}")

        # Output folder is created only after the native helper compiles successfully.
        output = create_output_dir(source)
        for path in files:
            # Relative path preserves subfolders for directory input.
            relative = path.relative_to(source_root)
            # Each source file receives a distinct output base.
            destination = output_path(output, relative, reserved_outputs)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Extension selects PDFKit for documents and AppKit for raster images.
                mode = "pdf" if path.suffix.lower() == ".pdf" else "image"
                result = subprocess.run(
                    [str(helper), mode, str(path), str(destination), str(max_pixels), str(quality)],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                # The native encoder returns the real output path, including its extension.
                actual_destination = Path(result.stdout.strip())
                compressed_total += actual_destination.stat().st_size
                print(
                    f"完成：{relative} -> {actual_destination.relative_to(output)} "
                    f"({actual_destination.stat().st_size / 1024**2:.2f} MB)"
                )
            except (OSError, subprocess.CalledProcessError) as error:
                failures += 1
                for possible_output in output.glob(f"{destination.name}.*"):
                    possible_output.unlink(missing_ok=True)
                # Capture native diagnostic text without dumping command details.
                details = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
                print(f"处理失败：{relative}（{details}）", file=sys.stderr)

    print(f"\n输出目录：{output}")
    print(f"合计：{compressed_total / 1024**2:.2f} MB；目标：{target_bytes / 1024**2:.2f} MB")
    if compressed_total > target_bytes:
        print("提示：实际合计超过目标；本脚本每份 PDF 只压缩一次以优先保证速度。", file=sys.stderr)
    if failures:
        print(f"有 {failures} 个文件处理失败。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
