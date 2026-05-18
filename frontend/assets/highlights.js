/**
 * 文章正文划线标记 + 弹窗评论/跟帖
 */
const ArticleHighlights = (() => {
    let articleId = null;
    let contentEl = null;
    let highlights = [];
    let ensureGuest = null;
    let getGuestName = null;
    let isAdmin = () => false;

    let contextMenuEl = null;
    let pendingSelection = null;
    let pendingRange = null;
    let cachedSelection = null;
    /** @type {Map<string, Range>} 评论提交前暂存选区，保证高亮位置与选中一致 */
    let pendingRangeByHighlightId = new Map();
    let activeHighlightId = null;
    let replyParentId = null;

    const onDocumentClick = () => hideContextMenu();
    const onDocumentScroll = () => hideContextMenu();
    const onDocumentKeydown = (e) => {
        if (e.key === 'Escape') hideContextMenu();
    };

    function escapeHtml(s) {
        return String(s)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;');
    }

    function formatTime(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return String(iso);
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    let textIndexCache = null;

    function isAnchorSkippedElement(el) {
        if (!el || el === contentEl) return false;
        return el.classList?.contains('read-mile') || el.classList?.contains('hl-mark');
    }

    function isAnchorSkippedNode(node) {
        let el = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
        while (el && el !== contentEl) {
            if (isAnchorSkippedElement(el)) return true;
            el = el.parentElement;
        }
        return false;
    }

    function invalidateTextIndex() {
        textIndexCache = null;
    }

    /** 构建正文锚点文本（排除 TOC 时间刻度与已有高亮壳层） */
    function getTextIndex() {
        if (textIndexCache) return textIndexCache;
        const map = [];
        const parts = [];
        if (!contentEl) {
            textIndexCache = { text: '', map };
            return textIndexCache;
        }
        const walker = document.createTreeWalker(contentEl, NodeFilter.SHOW_TEXT, {
            acceptNode(node) {
                return isAnchorSkippedNode(node)
                    ? NodeFilter.FILTER_REJECT
                    : NodeFilter.FILTER_ACCEPT;
            }
        });
        let node;
        while ((node = walker.nextNode())) {
            const t = node.textContent || '';
            for (let i = 0; i < t.length; i++) {
                map.push({ node, offset: i });
            }
            parts.push(t);
        }
        textIndexCache = { text: parts.join(''), map };
        return textIndexCache;
    }

    function getPlainText() {
        return getTextIndex().text;
    }

    function rangeToCanonicalIndices(range) {
        if (!range || range.collapsed) return { startIdx: -1, endIdx: -1 };
        const { map } = getTextIndex();
        let startIdx = -1;
        let endIdx = -1;
        for (let i = 0; i < map.length; i++) {
            const m = map[i];
            if (m.node === range.startContainer && m.offset === range.startOffset) startIdx = i;
            /* Range 的 endOffset 为排他边界：落在该 offset 的字符不包含在选区内 */
            if (m.node === range.endContainer && m.offset === range.endOffset) {
                endIdx = i;
                break;
            }
        }
        if (startIdx < 0) return { startIdx: -1, endIdx: -1 };
        if (endIdx < 0) {
            endIdx = startIdx + range.toString().length;
        }
        if (endIdx <= startIdx) return { startIdx: -1, endIdx: -1 };
        return { startIdx, endIdx };
    }

    function anchorStorageKey(highlightId) {
        return `hl-anchor:${articleId}:${highlightId}`;
    }

    function saveTextAnchor(highlightId, range) {
        if (!articleId || !highlightId || !range) return;
        const { startIdx, endIdx } = rangeToCanonicalIndices(range);
        if (startIdx < 0 || endIdx <= startIdx) return;
        try {
            sessionStorage.setItem(
                anchorStorageKey(highlightId),
                JSON.stringify({
                    startIdx,
                    endIdx,
                    exact: range.toString()
                })
            );
        } catch {
            /* sessionStorage 满或不可用 */
        }
    }

    function loadTextAnchor(highlightId) {
        if (!articleId) return null;
        try {
            const raw = sessionStorage.getItem(anchorStorageKey(highlightId));
            return raw ? JSON.parse(raw) : null;
        } catch {
            return null;
        }
    }

    function textNodeIntersectsRange(node, range) {
        try {
            const nodeRange = document.createRange();
            nodeRange.selectNodeContents(node);
            return (
                range.compareBoundaryPoints(Range.END_TO_START, nodeRange) < 0 &&
                range.compareBoundaryPoints(Range.START_TO_END, nodeRange) > 0
            );
        } catch {
            return false;
        }
    }

    /** 去掉选区首尾空白，避免存库文本与 DOM 包裹范围不一致 */
    function trimRangeWhitespace(range) {
        const segments = collectTextSegments(range);
        if (!segments.length) return null;

        let full = segments.map((s) => s.node.textContent.slice(s.start, s.end)).join('');
        const trimmed = full.trim();
        if (trimmed.length < 2) return null;

        let lead = full.length - full.trimStart().length;
        let trail = full.length - full.trimEnd().length;
        const segs = segments.map((s) => ({ ...s }));

        while (lead > 0 && segs.length) {
            const s = segs[0];
            const len = s.end - s.start;
            if (lead >= len) {
                lead -= len;
                segs.shift();
                continue;
            }
            segs[0] = { node: s.node, start: s.start + lead, end: s.end };
            lead = 0;
        }
        while (trail > 0 && segs.length) {
            const s = segs[segs.length - 1];
            const len = s.end - s.start;
            if (trail >= len) {
                trail -= len;
                segs.pop();
                continue;
            }
            segs[segs.length - 1] = { node: s.node, start: s.start, end: s.end - trail };
            trail = 0;
        }
        if (!segs.length) return null;

        const r = document.createRange();
        r.setStart(segs[0].node, segs[0].start);
        r.setEnd(segs[segs.length - 1].node, segs[segs.length - 1].end);
        return r;
    }

    function getSelectionContext() {
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount || sel.isCollapsed) return null;
        const raw = sel.getRangeAt(0);
        if (!contentEl || !contentEl.contains(raw.commonAncestorContainer)) return null;

        const range = trimRangeWhitespace(raw);
        if (!range) return null;

        const exact = range.toString();
        const { startIdx, endIdx } = rangeToCanonicalIndices(range);
        if (startIdx < 0) return null;
        const full = getPlainText();
        return {
            exact_text: exact,
            prefix_text: full.slice(Math.max(0, startIdx - 200), startIdx),
            suffix_text: full.slice(endIdx, Math.min(full.length, endIdx + 200)),
            range,
            startIdx,
            endIdx
        };
    }

    function findBestOffset(exact, prefix, suffix) {
        const full = getPlainText();
        if (!exact) return -1;
        const needle = (prefix || '') + exact + (suffix || '');
        let idx = full.indexOf(needle);
        if (idx >= 0) return idx + (prefix || '').length;

        const candidates = [];
        let pos = 0;
        while ((pos = full.indexOf(exact, pos)) !== -1) {
            candidates.push(pos);
            pos += 1;
        }
        if (!candidates.length) return -1;
        if (candidates.length === 1) return candidates[0];

        const preNeedle = (prefix || '').slice(-48);
        const sufNeedle = (suffix || '').slice(0, 48);
        let best = candidates[0];
        let bestScore = -1;
        for (const c of candidates) {
            let score = 0;
            if (preNeedle) {
                const pre = full.slice(Math.max(0, c - preNeedle.length), c);
                if (pre === preNeedle || pre.endsWith(preNeedle)) score += 2;
            }
            if (sufNeedle) {
                const suf = full.slice(c + exact.length, c + exact.length + sufNeedle.length);
                if (suf === sufNeedle || suf.startsWith(sufNeedle)) score += 2;
            }
            if (score > bestScore) {
                bestScore = score;
                best = c;
            }
        }
        return best;
    }

    function collectTextSegments(range) {
        const segments = [];
        if (!range || range.collapsed || !contentEl) return segments;

        const walker = document.createTreeWalker(contentEl, NodeFilter.SHOW_TEXT, null);
        let node;
        while ((node = walker.nextNode())) {
            if (!textNodeIntersectsRange(node, range)) continue;
            let start = 0;
            let end = node.textContent.length;
            if (node === range.startContainer) start = range.startOffset;
            if (node === range.endContainer) end = range.endOffset;
            if (start < end) segments.push({ node, start, end });
        }
        return segments;
    }

    function wrapTextSegments(segments, highlightId) {
        if (!segments.length) return false;
        for (let i = segments.length - 1; i >= 0; i--) {
            const { node, start, end } = segments[i];
            const r = document.createRange();
            r.setStart(node, start);
            r.setEnd(node, end);
            const span = document.createElement('span');
            span.className = 'hl-mark';
            span.dataset.highlightId = highlightId;
            r.surroundContents(span);
        }
        return true;
    }

    function wrapRange(range, highlightId) {
        return wrapTextSegments(collectTextSegments(range), highlightId);
    }

    function wrapAtIndices(startIdx, endIdx, highlightId) {
        const { map } = getTextIndex();
        if (startIdx < 0 || endIdx <= startIdx || endIdx > map.length) return false;
        const segments = [];
        let i = startIdx;
        while (i < endIdx) {
            const node = map[i].node;
            let start = map[i].offset;
            let end = start + 1;
            i++;
            while (i < endIdx && map[i].node === node) {
                end = map[i].offset + 1;
                i++;
            }
            segments.push({ node, start, end });
        }
        invalidateTextIndex();
        return wrapTextSegments(segments, highlightId);
    }

    function wrapRangeAtOffset(startOffset, length, highlightId) {
        return wrapAtIndices(startOffset, startOffset + length, highlightId);
    }

    function hasComments(h) {
        return Array.isArray(h?.comments) && h.comments.length > 0;
    }

    function unwrapHighlight(highlightId) {
        contentEl
            .querySelectorAll(`.hl-mark[data-highlight-id="${highlightId}"]`)
            .forEach((m) => {
                const parent = m.parentNode;
                if (!parent) return;
                while (m.firstChild) parent.insertBefore(m.firstChild, m);
                parent.removeChild(m);
            });
        invalidateTextIndex();
    }

    function unwrapMarks() {
        contentEl.querySelectorAll('.hl-mark').forEach((m) => {
            const parent = m.parentNode;
            if (!parent) return;
            while (m.firstChild) parent.insertBefore(m.firstChild, m);
            parent.removeChild(m);
        });
        invalidateTextIndex();
    }

    function applyHighlightInDom(h) {
        if (!h?.id) return false;
        const exact = h.exact_text || '';
        if (!exact) return false;

        const stored = loadTextAnchor(h.id);
        if (stored && typeof stored.startIdx === 'number' && typeof stored.endIdx === 'number') {
            const { text } = getTextIndex();
            const slice = text.slice(stored.startIdx, stored.endIdx);
            if (slice === exact || slice.trim() === exact.trim()) {
                if (wrapAtIndices(stored.startIdx, stored.endIdx, h.id)) return true;
            }
        }

        const offset = findBestOffset(exact, h.prefix_text, h.suffix_text);
        if (offset < 0) return false;
        const ok = wrapRangeAtOffset(offset, exact.length, h.id);
        if (ok) {
            try {
                sessionStorage.setItem(
                    anchorStorageKey(h.id),
                    JSON.stringify({
                        startIdx: offset,
                        endIdx: offset + exact.length,
                        exact
                    })
                );
            } catch {
                /* ignore */
            }
        }
        return ok;
    }

    function paintHighlight(h, rangeOpt) {
        if (!h?.id || !contentEl) return false;
        unwrapHighlight(h.id);
        invalidateTextIndex();

        if (rangeOpt) {
            try {
                if (contentEl.contains(rangeOpt.startContainer)) {
                    const { startIdx, endIdx } = rangeToCanonicalIndices(rangeOpt);
                    if (startIdx >= 0 && wrapAtIndices(startIdx, endIdx, h.id)) {
                        saveTextAnchor(h.id, rangeOpt);
                        return true;
                    }
                    if (wrapRange(rangeOpt, h.id)) {
                        saveTextAnchor(h.id, rangeOpt);
                        return true;
                    }
                }
            } catch {
                /* 选区失效时回退到文本定位 */
            }
        }

        const ok = applyHighlightInDom(h);
        return ok && contentEl.querySelector(`.hl-mark[data-highlight-id="${h.id}"]`) != null;
    }

    function revealHighlight(highlightId) {
        const marks = contentEl.querySelectorAll(`.hl-mark[data-highlight-id="${highlightId}"]`);
        if (!marks.length) return;
        marks.forEach((m) => m.classList.add('hl-active'));
        marks[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function applyAllHighlights() {
        if (!contentEl) return;
        unwrapMarks();
        invalidateTextIndex();
        highlights.filter(hasComments).forEach((h) => applyHighlightInDom(h));
    }

    function getHighlightById(id) {
        return highlights.find((h) => h.id === id) || null;
    }

    function upsertHighlight(h) {
        const i = highlights.findIndex((x) => x.id === h.id);
        if (i >= 0) highlights[i] = h;
        else highlights.push(h);
    }

    function ensureContextMenu() {
        if (contextMenuEl) return contextMenuEl;
        contextMenuEl = document.createElement('div');
        contextMenuEl.className = 'hl-context-menu';
        contextMenuEl.setAttribute('role', 'menu');
        document.body.appendChild(contextMenuEl);
        return contextMenuEl;
    }

    function hideContextMenu() {
        contextMenuEl?.classList.remove('hl-visible');
    }

    function positionContextMenu(x, y) {
        const menu = ensureContextMenu();
        menu.style.left = '0';
        menu.style.top = '0';
        menu.classList.add('hl-visible');

        const pad = 8;
        const rect = menu.getBoundingClientRect();
        let left = x;
        let top = y;
        if (left + rect.width > window.innerWidth - pad) {
            left = Math.max(pad, window.innerWidth - rect.width - pad);
        }
        if (top + rect.height > window.innerHeight - pad) {
            top = Math.max(pad, window.innerHeight - rect.height - pad);
        }
        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
    }

    function showContextMenu(x, y, items) {
        const menu = ensureContextMenu();
        menu.innerHTML = '';
        items.forEach(({ label, icon, action }) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'hl-context-item';
            btn.setAttribute('role', 'menuitem');
            btn.innerHTML = icon
                ? `<i class="fas ${icon}" aria-hidden="true"></i><span>${escapeHtml(label)}</span>`
                : `<span>${escapeHtml(label)}</span>`;
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                hideContextMenu();
                action();
            });
            menu.appendChild(btn);
        });
        positionContextMenu(x, y);
    }

    function captureSelectionRange() {
        const sel = window.getSelection();
        if (!sel?.rangeCount) return null;
        const raw = sel.getRangeAt(0);
        if (!contentEl?.contains(raw.commonAncestorContainer)) return null;
        return trimRangeWhitespace(raw);
    }

    function onContentMouseUp() {
        const ctx = getSelectionContext();
        cachedSelection = ctx;
        pendingRange = ctx?.range || captureSelectionRange();
    }

    function onContentContextMenu(e) {
        const mark = e.target.closest?.('.hl-mark');
        if (mark && contentEl?.contains(mark)) {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, [
                {
                    label: '查看划线讨论',
                    icon: 'fa-comments',
                    action: () => openModal(mark.dataset.highlightId)
                }
            ]);
            return;
        }

        const ctx = getSelectionContext() || cachedSelection;
        if (!ctx) return;

        e.preventDefault();
        pendingSelection = ctx;
        cachedSelection = ctx;
        pendingRange = captureSelectionRange() || pendingRange;
        showContextMenu(e.clientX, e.clientY, [
            {
                label: '划线并评论',
                icon: 'fa-highlighter',
                action: onCreateHighlightClick
            }
        ]);
    }

    function requireGuestThen(fn) {
        if (isAdmin() || getGuestName?.()) {
            return Promise.resolve().then(fn);
        }
        if (ensureGuest) {
            return new Promise((resolve) => {
                ensureGuest(() => resolve(fn()));
            });
        }
        return Promise.resolve().then(fn);
    }

    async function onCreateHighlightClick() {
        const ctx = pendingSelection;
        const rangeClone = pendingRange ? pendingRange.cloneRange() : ctx?.range?.cloneRange() || null;
        hideContextMenu();
        window.getSelection()?.removeAllRanges();
        if (!ctx || !articleId) return;

        requireGuestThen(async () => {
            try {
                const data = await API.postHighlight(articleId, {
                    exact_text: ctx.exact_text,
                    prefix_text: ctx.prefix_text,
                    suffix_text: ctx.suffix_text,
                    guest_name: isAdmin() ? '' : getGuestName?.() || ''
                });
                const h = data.highlight;
                if (h) {
                    upsertHighlight(h);
                    if (rangeClone) {
                        pendingRangeByHighlightId.set(h.id, rangeClone);
                        saveTextAnchor(h.id, rangeClone);
                    } else if (ctx.startIdx != null && ctx.endIdx != null) {
                        try {
                            sessionStorage.setItem(
                                anchorStorageKey(h.id),
                                JSON.stringify({
                                    startIdx: ctx.startIdx,
                                    endIdx: ctx.endIdx,
                                    exact: ctx.exact_text
                                })
                            );
                        } catch {
                            /* ignore */
                        }
                    }
                    openModal(h.id);
                }
            } catch (e) {
                alert(e?.message || '划线失败');
            }
        });
    }

    function renderCommentNode(c, depth) {
        const adminBadge = c.is_admin
            ? '<span class="ml-1 text-[10px] font-bold text-amber-700 dark:text-amber-300">博主</span>'
            : '';
        const replyBtn =
            depth < 4
                ? `<button type="button" class="hl-reply-btn" data-reply-to="${escapeHtml(c.id)}">回复</button>`
                : '';
        const repliesHtml = (c.replies || []).map((r) => renderCommentNode(r, depth + 1)).join('');
        const repliesWrap = repliesHtml ? `<div class="hl-replies">${repliesHtml}</div>` : '';
        return (
            '<div class="hl-comment">' +
            `<div class="hl-comment-meta">${escapeHtml(formatTime(c.created_at))} · ${escapeHtml(c.author || '')}${adminBadge}</div>` +
            `<div class="hl-comment-body">${escapeHtml(c.body || '')}</div>` +
            replyBtn +
            repliesWrap +
            '</div>'
        );
    }

    function renderCommentsList(comments) {
        if (!comments?.length) {
            return '<p class="hl-empty">暂无评论，写下第一条吧</p>';
        }
        return comments.map((c) => renderCommentNode(c, 0)).join('');
    }

    function bindReplyButtons(root) {
        root.querySelectorAll('.hl-reply-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                replyParentId = btn.getAttribute('data-reply-to');
                const input = document.getElementById('hl-comment-input');
                if (input) {
                    input.focus();
                    input.placeholder = '回复评论...';
                }
            });
        });
    }

    function closeModal() {
        document.getElementById('hl-modal-root')?.remove();
        activeHighlightId = null;
        replyParentId = null;
        contentEl?.querySelectorAll('.hl-mark.hl-active').forEach((m) => m.classList.remove('hl-active'));
    }

    function buildModalDom(h) {
        const root = document.createElement('div');
        root.id = 'hl-modal-root';
        root.className = 'hl-modal-backdrop';

        const modal = document.createElement('div');
        modal.className = 'hl-modal';
        modal.setAttribute('role', 'dialog');

        const header = document.createElement('div');
        header.className = 'hl-modal-header';
        const title = document.createElement('h3');
        title.className = 'hl-modal-title';
        title.textContent = '划线讨论';
        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'hl-modal-close';
        closeBtn.setAttribute('aria-label', '关闭');
        closeBtn.innerHTML = '<i class="fas fa-times"></i>';
        header.append(title, closeBtn);

        const quote = document.createElement('blockquote');
        quote.className = 'hl-modal-quote';
        quote.textContent = h.exact_text || '';

        const body = document.createElement('div');
        body.id = 'hl-comments-wrap';
        body.className = 'hl-modal-body';
        body.innerHTML = renderCommentsList(h.comments);

        const footer = document.createElement('div');
        footer.className = 'hl-modal-footer';
        const textarea = document.createElement('textarea');
        textarea.id = 'hl-comment-input';
        textarea.rows = 3;
        textarea.placeholder = '写下你对这段内容的看法...';
        const err = document.createElement('p');
        err.id = 'hl-comment-error';
        err.className = 'hl-msg-error';
        const actions = document.createElement('div');
        actions.className = 'hl-modal-actions';
        const submit = document.createElement('button');
        submit.type = 'button';
        submit.id = 'hl-comment-submit';
        submit.className = 'hl-btn-primary';
        submit.textContent = '发表评论';
        actions.appendChild(submit);
        footer.append(textarea, err, actions);

        modal.append(header, quote, body, footer);
        root.appendChild(modal);
        return root;
    }

    function openModal(highlightId) {
        const h = getHighlightById(highlightId);
        if (!h) return;

        replyParentId = null;
        closeModal();
        activeHighlightId = highlightId;

        contentEl?.querySelectorAll('.hl-mark.hl-active').forEach((m) => m.classList.remove('hl-active'));
        contentEl
            ?.querySelector(`.hl-mark[data-highlight-id="${highlightId}"]`)
            ?.classList.add('hl-active');

        const root = buildModalDom(h);
        document.body.appendChild(root);

        root.addEventListener('click', (e) => {
            if (e.target === root) closeModal();
        });
        root.querySelector('.hl-modal-close')?.addEventListener('click', closeModal);
        root.querySelector('#hl-comment-submit')?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            submitModalComment();
        });
        root.querySelector('.hl-modal')?.addEventListener('click', (e) => e.stopPropagation());
        bindReplyButtons(root);
    }

    async function submitModalComment() {
        const errEl = document.getElementById('hl-comment-error');
        const input = document.getElementById('hl-comment-input');
        const btn = document.getElementById('hl-comment-submit');
        if (errEl) errEl.textContent = '';
        const body = (input?.value || '').trim();
        if (!body) {
            if (errEl) errEl.textContent = '请填写评论内容';
            return;
        }
        if (!activeHighlightId || !btn) return;

        const doPost = async () => {
            const origLabel = btn.textContent;
            btn.disabled = true;
            btn.textContent = '提交中...';
            try {
                const data = await API.postHighlightComment(
                    activeHighlightId,
                    body,
                    isAdmin() ? '' : getGuestName?.() || '',
                    replyParentId
                );
                const h = data.highlight;
                if (h) {
                    upsertHighlight(h);
                    const storedRange = pendingRangeByHighlightId.get(activeHighlightId);
                    const alreadyPainted = contentEl.querySelector(
                        `.hl-mark[data-highlight-id="${activeHighlightId}"]`
                    );
                    if (!alreadyPainted) {
                        paintHighlight(h, storedRange || null);
                    }
                    pendingRangeByHighlightId.delete(activeHighlightId);
                    revealHighlight(h.id);

                    const wrap = document.getElementById('hl-comments-wrap');
                    if (wrap) wrap.innerHTML = renderCommentsList(h.comments);
                    if (input) {
                        input.value = '';
                        input.placeholder = '写下你对这段内容的看法...';
                    }
                    replyParentId = null;
                    bindReplyButtons(document.getElementById('hl-modal-root'));
                }
            } catch (e) {
                if (errEl) errEl.textContent = e?.message || '发表失败';
            } finally {
                btn.disabled = false;
                btn.textContent = origLabel;
            }
        };

        try {
            await requireGuestThen(doPost);
        } catch (e) {
            if (errEl) errEl.textContent = e?.message || '发表失败';
            btn.disabled = false;
        }
    }

    function onContentClick(e) {
        const mark = e.target.closest?.('.hl-mark');
        if (!mark || !contentEl?.contains(mark)) return;
        const id = mark.dataset.highlightId;
        if (id) openModal(id);
    }

    function init(opts) {
        articleId = opts.articleId;
        contentEl = opts.contentEl;
        highlights = Array.isArray(opts.highlights) ? opts.highlights.slice() : [];
        ensureGuest = opts.ensureGuest || null;
        getGuestName = opts.getGuestName || null;
        isAdmin = opts.isAdmin || (() => false);

        if (!contentEl || !articleId) return;

        invalidateTextIndex();
        applyAllHighlights();

        contentEl.addEventListener('mouseup', onContentMouseUp);
        contentEl.addEventListener('contextmenu', onContentContextMenu);
        contentEl.addEventListener('click', onContentClick);
        document.addEventListener('click', onDocumentClick);
        document.addEventListener('scroll', onDocumentScroll, true);
        document.addEventListener('keydown', onDocumentKeydown);
    }

    function destroy() {
        contentEl?.removeEventListener('mouseup', onContentMouseUp);
        contentEl?.removeEventListener('contextmenu', onContentContextMenu);
        contentEl?.removeEventListener('click', onContentClick);
        document.removeEventListener('click', onDocumentClick);
        document.removeEventListener('scroll', onDocumentScroll, true);
        document.removeEventListener('keydown', onDocumentKeydown);
        contextMenuEl?.remove();
        contextMenuEl = null;
        cachedSelection = null;
        pendingSelection = null;
        pendingRange = null;
        pendingRangeByHighlightId.clear();
        closeModal();
    }

    return { init, destroy, applyAllHighlights, openModal };
})();
