"""
HTML Export Module for NoteDiscovery
Generates standalone HTML files for notes with embedded images and styling.
Used by both /api/export (download) and /share (public sharing) endpoints.

Note: Only images are embedded as base64. Audio, video, and PDF files are
replaced with placeholder HTML since they would make exports too large.
"""

import base64
import logging
import re
from html import escape
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import quote
import mimetypes

# Import shared media type definitions and scanner from utils to avoid duplication
from backend.utils import MEDIA_EXTENSIONS, get_media_type, scan_notes_fast_walk

logger = logging.getLogger("uvicorn.error")


def attachment_content_disposition(filename: str) -> str:
    """
    Content-Disposition for a downloaded file.

    Same rule as Starlette's FileResponse, which the zip archive already uses.
    A name that can sit in an HTTP header unchanged stays in filename="...".
    Anything else (a space, an accent, text outside Latin-1) is sent as
    filename*=utf-8''... so the header stays Latin-1 and the browser keeps
    the original name.
    """
    encoded = quote(filename)
    if encoded != filename:
        return f"attachment; filename*=utf-8''{encoded}"
    return f'attachment; filename="{filename}"'

# Same stylesheet as the in-app preview pane. Inlined so downloaded HTML stays
# standalone, and so shared notes cannot drift from what the author sees.
_MARKDOWN_PREVIEW_CSS_PATH = Path(__file__).parent.parent / "frontend" / "markdown-preview.css"


def load_markdown_preview_css() -> str:
    """Return the shared markdown-preview CSS, or log and return empty on failure."""
    try:
        return _MARKDOWN_PREVIEW_CSS_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Failed to read markdown preview CSS from %s: %s", _MARKDOWN_PREVIEW_CSS_PATH, e)
        return ""


# Regex used by parse_image_size_spec — see docstring below.
_SIZE_RE = re.compile(r'^(\d+)(?:[xX](\d+))?$')


def parse_image_size_spec(text: str, allow_solo: bool = False) -> Tuple[str, Optional[int], Optional[int]]:
    """
    Parse an Obsidian-style inline image size annotation.

    Returns (clean_alt, width, height). width/height are None when unspecified.
    A dimension of 0 is treated as "unset" (Obsidian convention: |0x200 means height only).

    Rules:
      "caption"              -> ("caption",     None, None)
      "caption|100"          -> ("caption",     100,  None)
      "caption|100x200"      -> ("caption",     100,  200)
      "100"     (solo)       -> ("",            100,  None)   # only when allow_solo=True
      "100x200" (solo)       -> ("",            100,  200)    # only when allow_solo=True

    allow_solo=True is for wikilinks where `![[img|100]]` unambiguously means
    "size 100" because there's no ambiguity with alt text. For standard markdown
    `![100](x)` the default (allow_solo=False) leaves "100" as alt text.
    """
    if not text:
        return "", None, None
    trimmed = text.strip()
    if allow_solo:
        solo = _SIZE_RE.match(trimmed)
        if solo:
            w = int(solo.group(1))
            h = int(solo.group(2)) if solo.group(2) is not None else None
            return (
                "",
                w if w > 0 else None,
                h if (h is not None and h > 0) else None,
            )
    idx = trimmed.rfind('|')
    if idx == -1:
        return trimmed, None, None
    size = trimmed[idx + 1:].strip()
    m = _SIZE_RE.match(size)
    if not m:
        return trimmed, None, None
    w = int(m.group(1))
    h = int(m.group(2)) if m.group(2) is not None else None
    return (
        trimmed[:idx].strip(),
        w if w > 0 else None,
        h if (h is not None and h > 0) else None,
    )


def get_media_as_base64(media_path: Path) -> Optional[Tuple[str, str]]:
    """
    Read a media file and return it as a base64 data URL.
    Returns tuple of (base64_url, media_type) or None if failed.
    """
    if not media_path.exists() or not media_path.is_file():
        return None
    
    # Get MIME type
    mime_type, _ = mimetypes.guess_type(str(media_path))
    if not mime_type:
        return None
    
    # Determine media type
    media_type = get_media_type(media_path.name)
    if not media_type:
        return None
    
    try:
        with open(media_path, 'rb') as f:
            media_data = f.read()
        base64_data = base64.b64encode(media_data).decode('utf-8')
        return (f"data:{mime_type};base64,{base64_data}", media_type)
    except Exception as e:
        logger.error("Failed to read media %s: %s", media_path, e)
        return None


# Legacy alias for backward compatibility
def get_image_as_base64(image_path: Path) -> Optional[str]:
    """Read an image file and return it as a base64 data URL."""
    result = get_media_as_base64(image_path)
    if result and result[1] in ('image', 'drawing'):
        return result[0]
    return None


def strip_frontmatter(content: str) -> str:
    """
    Remove YAML frontmatter from markdown content.
    Frontmatter is delimited by --- at the start and end.
    """
    if not content.strip().startswith('---'):
        return content
    
    lines = content.split('\n')
    if lines[0].strip() != '---':
        return content
    
    # Find closing ---
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end_idx = i
            break
    
    if end_idx == -1:
        return content
    
    # Remove frontmatter and return the rest
    return '\n'.join(lines[end_idx + 1:]).strip()


def find_media_in_attachments(media_name: str, note_folder: Path, notes_dir: Path) -> Optional[Path]:
    """
    Search for a media file in common attachment locations.
    Returns the resolved path if found, None otherwise.
    """
    # Common locations to search for media (fast path)
    search_paths = [
        note_folder / media_name,                          # Same folder as note
        note_folder / '_attachments' / media_name,         # Note's _attachments folder
        notes_dir / '_attachments' / media_name,           # Root _attachments folder
    ]
    
    # Also search in parent folders' _attachments (for nested notes)
    current = note_folder
    while current != notes_dir and current.parent != current:
        search_paths.append(current / '_attachments' / media_name)
        current = current.parent
    
    for path in search_paths:
        resolved = path.resolve()
        if resolved.exists() and resolved.is_file():
            # Security: ensure path is within notes_dir
            try:
                resolved.relative_to(notes_dir.resolve())
                return resolved
            except ValueError:
                continue
    
    # Fallback: search all _attachments folders recursively (slower but thorough)
    # This handles cross-folder media references like in Obsidian
    try:
        _files, folders = scan_notes_fast_walk(str(notes_dir), include_media=False)
        for folder in folders:
            if folder == '_attachments' or folder.endswith('/_attachments'):
                attachment_folder = notes_dir / folder
                candidate = attachment_folder / media_name
                if candidate.exists() and candidate.is_file():
                    try:
                        candidate.resolve().relative_to(notes_dir.resolve())
                        return candidate.resolve()
                    except ValueError:
                        continue
    except Exception:
        pass  # Ignore errors in recursive search
    
    return None


# Legacy alias
def find_image_in_attachments(image_name: str, note_folder: Path, notes_dir: Path) -> Optional[Path]:
    return find_media_in_attachments(image_name, note_folder, notes_dir)


def generate_media_placeholder(media_type: str, alt_text: str) -> str:
    """Generate a placeholder for non-embeddable media (audio, video, PDF)."""
    safe_alt = alt_text.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
    
    icons = {'audio': '🎵', 'video': '🎬', 'document': '📄'}
    labels = {'audio': 'Audio file', 'video': 'Video file', 'document': 'PDF document'}
    icon = icons.get(media_type, '📎')
    label = labels.get(media_type, 'Media file')
    
    return f'''<div style="margin:1.5rem 0;padding:1.5rem;background:linear-gradient(135deg,var(--bg-tertiary,#f8f9fa) 0%,var(--bg-secondary,#e9ecef) 100%);border:1px solid var(--border-primary,#dee2e6);border-radius:0.5rem;display:flex;align-items:center;gap:1rem;">
<span style="font-size:2rem;">{icon}</span>
<div>
<div style="font-weight:600;color:var(--text-primary,#212529);">{safe_alt}</div>
<div style="font-size:0.875rem;color:var(--text-secondary,#6c757d);">{label} — not available in exported view</div>
</div>
</div>'''


def process_media_for_export(markdown_content: str, note_folder: Path, notes_dir: Path) -> str:
    """
    Process all media references in markdown for standalone HTML export.
    
    Handles:
    - Standard markdown images: ![alt](path)
    - Wikilink media: ![[file.png]] or ![[file.mp3|alt text]]
    
    Behavior by media type:
    - Images (jpg, png, gif, webp): Embedded as base64 data URLs
    - Audio/Video/PDF: Replaced with styled placeholder HTML (not embedded - too large)
    """
    
    # First, handle wikilink media: ![[file.png]] or ![[file.mp3|alt text]]
    # Also supports Obsidian-style inline sizing:
    #   ![[img.jpg|100]]           -> width 100
    #   ![[img.jpg|100x200]]       -> width 100, height 200
    #   ![[img.jpg|caption|100]]   -> alt "caption", width 100
    wikilink_pattern = r'!\[\[([^\]|]+)(?:\|([^\]]+))?\]\]'
    
    def replace_wikilink_media(match):
        media_name = match.group(1).strip()
        raw_alt = match.group(2).strip() if match.group(2) else ''
        # Wikilink alt-group: solo `|<digits>` is a size, no ambiguity.
        clean_alt, width, height = parse_image_size_spec(raw_alt, allow_solo=True)
        alt_text = clean_alt if clean_alt else media_name.split('/')[-1].rsplit('.', 1)[0]
        
        # Check media type first
        media_type = get_media_type(media_name)
        
        # For non-image media (audio, video, PDF), show placeholder without embedding.
        # Size specs are silently dropped — they only make sense for images.
        if media_type in ('audio', 'video', 'document'):
            return generate_media_placeholder(media_type, alt_text)
        
        # For images, embed as base64
        resolved_path = find_media_in_attachments(media_name, note_folder, notes_dir)
        
        if resolved_path:
            base64_url = get_image_as_base64(resolved_path)
            if base64_url:
                # If a size was specified, emit raw <img> HTML directly so the
                # dimensions survive the markdown round-trip. Marked.js passes
                # raw HTML through and DOMPurify allows width/height on <img>.
                # Without size, keep emitting standard markdown for consistency.
                if width or height:
                    safe_alt = alt_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                    # Emit width/height as BOTH attributes and inline style; the
                    # style beats stylesheet rules that otherwise force
                    # `height: auto` (e.g. Tailwind Preflight in the app; harmless
                    # in the standalone export).
                    size_attrs = ''
                    style_pieces = []
                    if width:
                        size_attrs += f' width="{width}"'
                        style_pieces.append(f'width:{width}px')
                    if height:
                        size_attrs += f' height="{height}"'
                        style_pieces.append(f'height:{height}px')
                    if style_pieces:
                        size_attrs += f' style="{";".join(style_pieces)}"'
                    return f'<img src="{base64_url}" alt="{safe_alt}" title="{safe_alt}"{size_attrs}>'
                return f'![{alt_text}]({base64_url})'
        
        # Image not found
        return f'<span style="color:var(--text-tertiary,#999);opacity:0.7;" title="Image not found">🖼️ {alt_text}</span>'
    
    markdown_content = re.sub(wikilink_pattern, replace_wikilink_media, markdown_content)
    
    # Then, handle standard markdown images: ![alt](path)
    img_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    
    def replace_media(match):
        alt_text = match.group(1)
        media_path = match.group(2)
        
        # Handle external URLs
        if media_path.startswith(('http://', 'https://')):
            # Check if it's a PDF - generate styled external link
            media_type = get_media_type(media_path)
            if media_type == 'document':
                display_name = alt_text or Path(media_path).stem
                safe_name = display_name.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
                safe_url = media_path.replace('"', '&quot;')
                return f'''<a href="{safe_url}" target="_blank" rel="noopener noreferrer" style="display:flex;flex-direction:column;gap:0.25rem;padding:1rem 1.25rem;margin:1rem 0;background:linear-gradient(135deg,var(--bg-tertiary,#f8f9fa) 0%,var(--bg-secondary,#e9ecef) 100%);border:1px solid var(--border-primary,#dee2e6);border-radius:0.5rem;color:var(--text-primary,#212529);text-decoration:none;">
<span style="font-weight:600;">📄 {safe_name}</span>
<span style="font-size:0.75rem;color:var(--text-secondary,#6c757d);">Opens in new tab</span>
</a>'''
            # Other external media: keep as-is (will show as broken image)
            return match.group(0)
        
        # Skip already-embedded base64
        if media_path.startswith('data:'):
            return match.group(0)
        
        # Skip empty paths (from failed wikilink conversion)
        if not media_path:
            return match.group(0)
        
        # Check media type first
        media_type = get_media_type(media_path)
        display_alt = alt_text or Path(media_path).stem
        
        # For non-image media (audio, video, PDF), show placeholder without embedding
        if media_type in ('audio', 'video', 'document'):
            return generate_media_placeholder(media_type, display_alt)
        
        # For images, proceed with base64 embedding
        # Handle /api/media/ or legacy /api/images/ paths (convert to filesystem paths)
        if media_path.startswith('/api/media/'):
            relative_path = media_path[len('/api/media/'):]
            resolved_path = (notes_dir / relative_path).resolve()
        elif media_path.startswith('/api/images/'):
            # Legacy path support for backward compatibility
            relative_path = media_path[len('/api/images/'):]
            resolved_path = (notes_dir / relative_path).resolve()
        else:
            # Try to resolve the media path relative to note folder
            resolved_path = (note_folder / media_path).resolve()
        
        # If not found, try the attachment search
        if not resolved_path.exists():
            # Extract just the filename and search
            media_name = Path(media_path).name
            resolved_path = find_media_in_attachments(media_name, note_folder, notes_dir)
            if not resolved_path:
                return match.group(0)  # Keep original if not found
        
        # Security: ensure path is within notes_dir
        try:
            resolved_path.relative_to(notes_dir.resolve())
        except ValueError:
            # Path is outside notes_dir, skip
            return match.group(0)
        
        # Get base64 data for image
        base64_url = get_image_as_base64(resolved_path)
        if base64_url:
            return f'![{display_alt}]({base64_url})'
        
        # Image not found, keep original
        return match.group(0)
    
    markdown_content = re.sub(img_pattern, replace_media, markdown_content)
    
    return markdown_content


# Legacy alias for backward compatibility
# Legacy alias for backward compatibility
def embed_images_as_base64(markdown_content: str, note_folder: Path, notes_dir: Path) -> str:
    """Alias for process_media_for_export (legacy name kept for compatibility)."""
    return process_media_for_export(markdown_content, note_folder, notes_dir)


def convert_wikilinks_to_html(markdown_content: str) -> str:
    """
    Convert wikilinks [[note]] or [[note|display text]] to HTML links.
    In standalone export mode, these are non-functional decorative links.
    Wikilinks inside fenced or inline code are left literal, mirroring the
    in-app preview pipeline.
    """
    wikilink_pattern = r'(?<!!)\[\[([^\]|]+)(?:\|([^\]]+))?\]\]'

    def replace_wikilink(match):
        target = match.group(1).strip()
        display = match.group(2).strip() if match.group(2) else target
        # User-controlled note text lands in HTML attributes/body on /share and
        # export pages — escape so a crafted wikilink cannot break out of the tag.
        safe_target = escape(target, quote=True)
        safe_display = escape(display)
        return (
            f'<a href="#" class="wikilink" title="{safe_target}" '
            f'style="color: var(--accent-primary, #0366d6); text-decoration: none; '
            f'border-bottom: 1px dashed currentColor;">{safe_display}</a>'
        )

    code_blocks: list = []

    def stash(match):
        code_blocks.append(match.group(0))
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    protected = re.sub(r'```[\s\S]*?```', stash, markdown_content)
    protected = re.sub(r'`[^`]+`', stash, protected)
    converted = re.sub(wikilink_pattern, replace_wikilink, protected)
    return re.sub(r'\x00CODEBLOCK(\d+)\x00', lambda m: code_blocks[int(m.group(1))], converted)


def generate_export_html(
    title: str,
    content: str,
    theme_css: str,
    is_dark: bool = False,
    show_print_button: bool = False,
    local_assets: bool = False
) -> str:
    """
    Generate a standalone HTML document for a note.
    Uses marked.js for client-side markdown rendering.
    Markdown element styles come from frontend/markdown-preview.css so
    exported and shared notes match the in-app preview.

    Args:
        title: The note title (for <title> and display)
        content: Raw markdown content (images should already be base64 embedded)
        theme_css: CSS content for theming
        is_dark: Whether using a dark theme (for Mermaid/Highlight.js)
        show_print_button: Whether to show a print button (for preview mode)
        local_assets: Load libraries from this server's /static/vendor/ instead of
            CDNs. Only safe when the page is served by us (shared links, print
            preview), never for a file the user downloads and opens elsewhere.

    Returns:
        Complete HTML document as string
    """
    # Escape content for JavaScript string
    escaped_content = (
        content
        .replace('\\', '\\\\')
        .replace('`', '\\`')
        .replace('$', '\\$')
        .replace('</', '<\\/')  # Prevent </script> breaking
    )
    # Filename stems are user-controlled; keep </title> / attribute breakouts out of <head>.
    safe_title = escape(title, quote=True)
    
    highlight_theme = 'github-dark' if is_dark else 'github'
    mermaid_theme = 'dark' if is_dark else 'default'

    # Versions must match scripts/vendor_assets.py, so that a page renders the same
    # whether it came from the vendored copies or from the CDNs.
    if local_assets:
        hljs_css = f'/static/vendor/highlight.js/styles/{highlight_theme}.min.css'
        hljs_js = '/static/vendor/highlight.js/highlight.min.js'
        marked_js = '/static/vendor/marked/marked.min.js'
        purify_js = '/static/vendor/dompurify/purify.min.js'
        mathjax_js = '/static/vendor/mathjax/tex-mml-chtml.js'
        mermaid_mjs = '/static/vendor/mermaid/mermaid.esm.min.mjs'
    else:
        hljs_base = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0'
        hljs_css = f'{hljs_base}/styles/{highlight_theme}.min.css'
        hljs_js = f'{hljs_base}/highlight.min.js'
        marked_js = 'https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js'
        purify_js = 'https://cdn.jsdelivr.net/npm/dompurify@3.0.8/dist/purify.min.js'
        mathjax_js = 'https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-mml-chtml.js'
        mermaid_mjs = 'https://cdn.jsdelivr.net/npm/mermaid@11.12.2/dist/mermaid.esm.min.mjs'
    
    preview_css = load_markdown_preview_css()

    # Print toolbar HTML (only shown in preview mode)
    print_toolbar_html = '''
    <div class="print-toolbar">
        <button onclick="window.print()" title="Print">
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"></path>
            </svg>
            Print
        </button>
        <button onclick="window.close()" title="Close">
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
            </svg>
            Close
        </button>
    </div>
''' if show_print_button else ''
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title}</title>
    
    <!-- Highlight.js for code syntax highlighting -->
    <link rel="stylesheet" href="{hljs_css}">
    <script src="{hljs_js}"></script>
    
    <!-- Marked.js for markdown parsing -->
    <script src="{marked_js}"></script>
    
    <!-- DOMPurify for HTML sanitization (XSS prevention) -->
    <script src="{purify_js}"></script>
    
    <!-- MathJax for LaTeX math rendering -->
    <script>
        MathJax = {{
            tex: {{
                inlineMath: [['\\\\(', '\\\\)'], ['$', '$']],
                displayMath: [['\\\\[', '\\\\]'], ['$$', '$$']],
                processEscapes: true,
                processEnvironments: true
            }},
            options: {{
                skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
            }},
            startup: {{
                pageReady: () => {{
                    return MathJax.startup.defaultPageReady().then(() => {{
                        // Highlight code blocks after MathJax is done
                        document.querySelectorAll('pre code:not(.language-mermaid)').forEach((block) => {{
                            hljs.highlightElement(block);
                        }});
                    }});
                }}
            }}
        }};
    </script>
    <script src="{mathjax_js}"></script>
    
    <!-- Mermaid.js for diagrams -->
    <script type="module">
        import mermaid from '{mermaid_mjs}';
        mermaid.initialize({{ 
            startOnLoad: false,
            theme: '{mermaid_theme}',
            securityLevel: 'strict',
            fontFamily: 'inherit',
            flowchart: {{ useMaxWidth: true }},
            sequence: {{ useMaxWidth: true }},
            gantt: {{ useMaxWidth: true }},
            state: {{ useMaxWidth: true }},
            er: {{ useMaxWidth: true }},
            pie: {{ useMaxWidth: true }},
            mindmap: {{ useMaxWidth: true }},
            gitGraph: {{ useMaxWidth: true }}
        }});
        
        // Render Mermaid diagrams after page load
        document.addEventListener('DOMContentLoaded', async () => {{
            const mermaidBlocks = document.querySelectorAll('pre code.language-mermaid');
            for (let i = 0; i < mermaidBlocks.length; i++) {{
                const block = mermaidBlocks[i];
                const pre = block.parentElement;
                try {{
                    const code = block.textContent;
                    const id = 'mermaid-diagram-' + i;
                    const {{ svg }} = await mermaid.render(id, code);
                    const container = document.createElement('div');
                    container.className = 'mermaid-rendered';
                    container.style.cssText = 'background-color: transparent; padding: 20px; text-align: center; overflow-x: auto;';
                    container.innerHTML = svg;
                    pre.parentElement.replaceChild(container, pre);
                }} catch (error) {{
                    console.error('Mermaid rendering error:', error);
                }}
            }}
        }});
    </script>
    
    <style>
        /* Theme CSS */
        {theme_css}
        
        /* Base styles */
        * {{
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 2rem;
            max-width: 900px;
            margin-left: auto;
            margin-right: auto;
            background-color: var(--bg-primary, #ffffff);
            color: var(--text-primary, #333333);
        }}
        
        /* Same element styles as the in-app preview */
        {preview_css}
        
        /* Copy button for code blocks */
        .markdown-preview pre {{
            position: relative;
        }}
        
        .copy-btn {{
            position: absolute;
            top: 0.5rem;
            right: 0.5rem;
            padding: 0.25rem 0.5rem;
            font-size: 0.75rem;
            background-color: var(--bg-secondary, #e1e4e8);
            color: var(--text-secondary, #586069);
            border: 1px solid var(--border-primary, #d0d7de);
            border-radius: 0.25rem;
            cursor: pointer;
            opacity: 0;
            transition: opacity 0.2s ease;
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        }}
        
        .markdown-preview pre:hover .copy-btn {{
            opacity: 1;
        }}
        
        .copy-btn:hover {{
            background-color: var(--accent-primary, #0366d6);
            color: white;
            border-color: var(--accent-primary, #0366d6);
        }}
        
        .copy-btn.copied {{
            background-color: #10b981;
            color: white;
            border-color: #10b981;
        }}
        
        @media (max-width: 768px) {{
            body {{
                padding: 1rem;
            }}
        }}
        
        @media print {{
            body {{
                padding: 0.5in;
                max-width: none;
            }}
            .print-toolbar {{
                display: none !important;
            }}
        }}
        
        /* Print toolbar (only shown in preview mode) */
        .print-toolbar {{
            position: fixed;
            top: 1rem;
            right: 1rem;
            z-index: 1000;
            display: flex;
            gap: 0.5rem;
            background: var(--bg-secondary, #f8f9fa);
            padding: 0.5rem;
            border-radius: 0.5rem;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
            border: 1px solid var(--border-primary, #dee2e6);
        }}
        
        .print-toolbar button {{
            display: flex;
            align-items: center;
            gap: 0.375rem;
            padding: 0.5rem 0.75rem;
            border: 1px solid var(--border-primary, #dee2e6);
            border-radius: 0.375rem;
            background: var(--bg-primary, #ffffff);
            color: var(--text-primary, #333333);
            cursor: pointer;
            font-size: 0.875rem;
            font-family: inherit;
            transition: background-color 0.15s, border-color 0.15s;
        }}
        
        .print-toolbar button:hover {{
            background: var(--bg-tertiary, #e9ecef);
            border-color: var(--accent-primary, #0366d6);
        }}
        
        .print-toolbar button svg {{
            width: 1rem;
            height: 1rem;
        }}
    </style>
</head>
<body>
    {print_toolbar_html}
    <div class="markdown-preview" id="content"></div>

    <script>
        // Protect LaTeX delimiters \\(...\\) and \\[...\\] from marked.js escaping
        marked.use({{
            extensions: [{{
                name: 'protectLatexMath',
                level: 'inline',
                start(src) {{ return src.match(/\\\\[\\(\\[]/)?.index; }},
                tokenizer(src) {{
                    const match = src.match(/^(\\\\[\\(\\[])([\\s\\S]*?)(\\\\[\\)\\]])/);
                    if (match) {{
                        return {{ type: 'html', raw: match[0], text: match[0] }};
                    }}
                }}
            }}]
        }});

        // Configure marked
        marked.setOptions({{
            gfm: true,
            breaks: true,
            headerIds: true,
            mangle: false
        }});

        // Raw markdown content
        const markdown = `{escaped_content}`;

        // GFM/GLFM callouts. Runs BEFORE code-block extraction so fences
        // nested in a callout blockquote lose their `> ` prefix on the closer
        // — otherwise the restored block sits inside <div class="callout-body">
        // without a valid CommonMark fence closer and runs unclosed.
        // Fence-aware so literal `> [!TIP]` inside a top-level code block
        // is not misread. Mirrors the in-app preview preprocessor.
        let processed;
        {{
            const CALLOUT_RE = /^>\\s*\\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\\]\\s*(.*)$/i;
            const CALLOUT_ICONS = {{ note: 'ℹ️', tip: '💡', important: '❗', warning: '⚠️', caution: '🛑' }};
            const CALLOUT_TITLES = {{ note: 'Note', tip: 'Tip', important: 'Important', warning: 'Warning', caution: 'Caution' }};
            const FENCE_OPEN_RE = /^\\s{{0,3}}(`{{3,}}|~{{3,}})/;
            const escapeAttr = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

            const srcLines = markdown.split('\\n');
            const outLines = [];
            let li = 0;
            let fenceChar = null;
            let fenceLen = 0;
            while (li < srcLines.length) {{
                const line = srcLines[li];
                if (fenceChar) {{
                    outLines.push(line);
                    const closeRe = new RegExp('^\\\\s{{0,3}}' + (fenceChar === '`' ? '`' : '~') + '{{' + fenceLen + ',}}\\\\s*$');
                    if (closeRe.test(line)) {{ fenceChar = null; fenceLen = 0; }}
                    li++;
                    continue;
                }}
                const fenceOpen = line.match(FENCE_OPEN_RE);
                if (fenceOpen) {{
                    fenceChar = fenceOpen[1][0];
                    fenceLen = fenceOpen[1].length;
                    outLines.push(line);
                    li++;
                    continue;
                }}
                const m = line.match(CALLOUT_RE);
                if (!m) {{ outLines.push(line); li++; continue; }}
                const type = m[1].toLowerCase();
                const title = escapeAttr((m[2] || '').trim() || CALLOUT_TITLES[type]);
                const icon = CALLOUT_ICONS[type];
                const bodyLines = [];
                li++;
                while (li < srcLines.length && srcLines[li].startsWith('>')) {{
                    bodyLines.push(srcLines[li].replace(/^>\\s?/, ''));
                    li++;
                }}
                outLines.push(
                    '',
                    '<div class="callout callout-' + type + '">',
                    '<div class="callout-title"><span class="callout-icon" aria-hidden="true">' + icon + '</span><span class="callout-title-text">' + title + '</span></div>',
                    '<div class="callout-body">',
                    '',
                    bodyLines.join('\\n'),
                    '',
                    '</div>',
                    '</div>',
                    ''
                );
            }}
            processed = outLines.join('\\n');

            // Escape same-line block-starters after a task marker so they
            // render as literal text. Matches Obsidian. Issue #247.
            processed = processed.replace(
                /^(\\s*[-*+]\\s+\\[[xX ]\\]\\s+)(\\d+)\\.(\\s)/gm,
                '$1$2\\\\.$3'
            );
            processed = processed.replace(
                /^(\\s*[-*+]\\s+\\[[xX ]\\]\\s+)([#>*+\\-])(\\s)/gm,
                '$1\\\\$2$3'
            );
        }}

        // Render markdown with XSS sanitization
        // DOMPurify strips scripts, iframes, and event handlers while allowing safe HTML/SVG
        const rawHtml = marked.parse(processed);
        const safeHtml = DOMPurify.sanitize(rawHtml);
        document.getElementById('content').innerHTML = safeHtml;

        // Apply Obsidian-style inline image sizing to images whose alt text
        // carries a `|<w>` or `|<w>x<h>` suffix. Wikilink images with a size
        // were already emitted with width/height attributes server-side; this
        // walker handles the standard-markdown case `![alt|100](x)`. Mirrors
        // parseImageSizeSpec() in frontend/app.js (allowSolo=false here, since
        // standard markdown `![100](x)` should stay as alt="100").
        document.querySelectorAll('.markdown-preview img').forEach(img => {{
            const rawAlt = img.getAttribute('alt') || '';
            const idx = rawAlt.lastIndexOf('|');
            if (idx === -1) return;
            const size = rawAlt.slice(idx + 1).trim();
            const m = size.match(/^(\\d+)(?:[xX](\\d+))?$/);
            if (!m) return;
            const w = parseInt(m[1], 10);
            const h = m[2] !== undefined ? parseInt(m[2], 10) : null;
            if (w > 0 && !img.hasAttribute('width')) {{
                img.setAttribute('width', String(w));
                img.style.width = w + 'px';
            }}
            if (h !== null && h > 0 && !img.hasAttribute('height')) {{
                img.setAttribute('height', String(h));
                img.style.height = h + 'px';
            }}
            const cleanAlt = rawAlt.slice(0, idx).trim();
            img.setAttribute('alt', cleanAlt);
            if (img.getAttribute('title') === rawAlt) img.setAttribute('title', cleanAlt);
        }});
        
        // Typeset math after content is inserted
        if (typeof MathJax !== 'undefined' && MathJax.typeset) {{
            MathJax.typeset();
        }}
        
        // Add copy buttons to code blocks
        document.querySelectorAll('.markdown-preview pre').forEach(pre => {{
            const btn = document.createElement('button');
            btn.className = 'copy-btn';
            btn.textContent = 'Copy';
            btn.addEventListener('click', async () => {{
                const code = pre.querySelector('code');
                if (code) {{
                    try {{
                        await navigator.clipboard.writeText(code.textContent);
                        btn.textContent = 'Copied!';
                        btn.classList.add('copied');
                        setTimeout(() => {{
                            btn.textContent = 'Copy';
                            btn.classList.remove('copied');
                        }}, 2000);
                    }} catch (err) {{
                        btn.textContent = 'Failed';
                        setTimeout(() => btn.textContent = 'Copy', 2000);
                    }}
                }}
            }});
            pre.appendChild(btn);
        }});
    </script>
</body>
</html>'''
    
    return html
