import logging
import subprocess
import shutil
from pathlib import Path

# LibreOffice 可执行文件候选路径（不同发行版/集群环境名称可能不同）
_LIBREOFFICE_CANDIDATES = ["libreoffice", "soffice"]


def _find_libreoffice():
    """在系统 PATH 中查找可用的 LibreOffice 可执行文件"""
    for name in _LIBREOFFICE_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def convert_md_to_pdf_via_word(md_abs_path: Path, pdf_abs_path: Path) -> str:
    """
    使用 LibreOffice headless 将文件转换为 PDF。

    为什么不用 Word COM：
        原实现依赖 pywin32 (win32com)，是 Windows 专属接口，
        在 Linux 服务器上无法安装和运行。

    LibreOffice 方案说明：
        LibreOffice 原生支持 Markdown 读取（内部会先转 HTML），
        通过 --headless --convert-to pdf 命令行无界面转换，中文支持良好。

    依赖：
        系统需安装 LibreOffice（如 CentOS: yum install libreoffice-headless libreoffice-writer）

    Args:
        md_abs_path (Path): 源 Markdown 文件的绝对路径
        pdf_abs_path (Path): 目标 PDF 文件的绝对路径

    Returns:
        str: 转换结果描述（成功/失败信息，供 Agent 直接反馈）
    """
    libreoffice = _find_libreoffice()
    if not libreoffice:
        return ("转换失败：未找到 LibreOffice。"
                "请在服务器上安装：yum install libreoffice-headless libreoffice-writer "
                "或 apt install libreoffice-writer")

    try:
        # LibreOffice 的 --convert-to 只能输出到指定目录，且输出文件名与源文件同名，
        # 因此先在目标 PDF 所在目录执行转换，再将重命名的临时产物移动为最终 PDF
        out_dir = pdf_abs_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [
                libreoffice,
                "--headless",                       # 无界面模式（服务器必备）
                "--convert-to", "pdf:writer_pdf_Export",  # 明确使用 Writer 的 PDF 导出器
                "--outdir", str(out_dir),           # 输出目录
                str(md_abs_path.resolve()),         # 源文件
            ],
            capture_output=True,
            text=True,
            timeout=120,  # 防止进程挂死
        )

        # LibreOffice 输出的 PDF 与源 md 同名，只是后缀变为 .pdf
        converted_pdf = md_abs_path.with_suffix(".pdf")

        if result.returncode != 0 or not converted_pdf.exists():
            logging.error(f"LibreOffice 转换失败: {result.stderr}")
            return f"转换失败: {result.stderr or 'LibreOffice 未生成输出文件'}"

        # 同名转换产物 → 重命名为用户指定的 pdf_abs_path
        if converted_pdf != pdf_abs_path:
            shutil.move(str(converted_pdf), str(pdf_abs_path))

        if pdf_abs_path.exists():
            return f"成功转换: {pdf_abs_path} (LibreOffice引擎)"
        return f"转换完成但未生成文件: {pdf_abs_path}"

    except subprocess.TimeoutExpired:
        return "转换失败: LibreOffice 执行超时（120秒）"
    except Exception as e:
        logging.error(f"LibreOffice 转换 PDF 失败: {e}", exc_info=True)
        return f"转换失败: {str(e)}"
