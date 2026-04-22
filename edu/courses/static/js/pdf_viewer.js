/* PDF viewer module (PDF.js) */
(function () {
    "use strict";

    const DEFAULTS = {
        urlState: true,
        pageWindow: 7,
        prefetchPadding: 2,
        maxDpr: 2,
        sendIntervalMs: 5000,
    };

    const instances = new Map();

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            if (timer) window.clearTimeout(timer);
            timer = window.setTimeout(() => {
                timer = null;
                fn.apply(this, args);
            }, delay);
        };
    }

    function throttle(fn, delay) {
        let last = 0;
        let trailing = null;
        return function (...args) {
            const now = Date.now();
            const remaining = delay - (now - last);
            if (remaining <= 0) {
                last = now;
                fn.apply(this, args);
            } else if (!trailing) {
                trailing = window.setTimeout(() => {
                    trailing = null;
                    last = Date.now();
                    fn.apply(this, args);
                }, remaining);
            }
        };
    }

    function createStore(initialState) {
        let state = { ...initialState };
        const listeners = new Set();
        return {
            getState: () => state,
            setState: (patch) => {
                state = { ...state, ...patch };
                listeners.forEach((listener) => listener(state));
            },
            subscribe: (listener) => {
                listeners.add(listener);
                return () => listeners.delete(listener);
            },
        };
    }

    class DocumentLoader {
        constructor(pdfjsLib, url) {
            this.pdfjsLib = pdfjsLib;
            this.url = url;
            this.loadingTask = null;
            this.doc = null;
        }

        async load() {
            if (!this.pdfjsLib || !this.url) return null;
            this.loadingTask = this.pdfjsLib.getDocument(this.url);
            this.doc = await this.loadingTask.promise;
            return this.doc;
        }

        async destroy() {
            try {
                if (this.doc) await this.doc.destroy();
            } catch (error) {
                // ignore
            }
            try {
                if (this.loadingTask) await this.loadingTask.destroy();
            } catch (error) {
                // ignore
            }
            this.doc = null;
            this.loadingTask = null;
        }
    }

    class LayoutManager {
        constructor(pageSpacing = 12) {
            this.pageSpacing = pageSpacing;
            this.pageSizes = [];
            this.pageOffsets = [];
            this.totalHeight = 0;
        }

        setPageSizes(pageSizes) {
            this.pageSizes = pageSizes.slice();
            this._recomputeOffsets();
        }

        updatePageSize(pageIndex, size) {
            if (!this.pageSizes[pageIndex]) return;
            this.pageSizes[pageIndex] = size;
            this._recomputeOffsets();
        }

        _recomputeOffsets() {
            const offsets = [];
            let cursor = 0;
            this.pageSizes.forEach((size, index) => {
                offsets[index] = cursor;
                cursor += (size.height || 0) + this.pageSpacing;
            });
            this.pageOffsets = offsets;
            this.totalHeight = Math.max(0, cursor - this.pageSpacing);
        }

        getPageOffset(pageIndex) {
            return this.pageOffsets[pageIndex] || 0;
        }

        getDocHeight() {
            return this.totalHeight || 1;
        }

        findPageAt(docY) {
            const offsets = this.pageOffsets;
            if (!offsets.length) return 0;
            let low = 0;
            let high = offsets.length - 1;
            while (low <= high) {
                const mid = Math.floor((low + high) / 2);
                if (offsets[mid] <= docY) {
                    if (mid === offsets.length - 1 || offsets[mid + 1] > docY) {
                        return mid;
                    }
                    low = mid + 1;
                } else {
                    high = mid - 1;
                }
            }
            return 0;
        }
    }

    class RenderQueue {
        constructor(renderFn, options) {
            this.renderFn = renderFn;
            this.options = options;
            this.queue = [];
            this.running = false;
            this.rendered = new Map();
            this.generation = 0;
        }

        reset() {
            this.queue = [];
            this.running = false;
            this.generation += 1;
        }

        schedule(pageNumber, priority = 0) {
            if (this.rendered.has(pageNumber)) return;
            if (this.queue.some((item) => item.pageNumber === pageNumber)) return;
            this.queue.push({ pageNumber, priority });
            this.queue.sort((a, b) => b.priority - a.priority);
            this._run();
        }

        markRendered(pageNumber, meta) {
            this.rendered.set(pageNumber, meta || {});
        }

        clearRendered(pageNumbers) {
            pageNumbers.forEach((pageNumber) => {
                this.rendered.delete(pageNumber);
            });
        }

        evict(allowedPages) {
            const allowed = new Set(allowedPages);
            this.rendered.forEach((meta, pageNumber) => {
                if (!allowed.has(pageNumber)) {
                    if (meta && typeof meta.dispose === "function") {
                        meta.dispose();
                    }
                    this.rendered.delete(pageNumber);
                }
            });
        }

        async _run() {
            if (this.running) return;
            this.running = true;
            const generation = this.generation;
            while (this.queue.length) {
                const item = this.queue.shift();
                if (!item) continue;
                try {
                    await this.renderFn(item.pageNumber, generation);
                } catch (error) {
                    // ignore render failures
                }
                if (generation !== this.generation) {
                    break;
                }
            }
            this.running = false;
        }
    }

    class PdfViewerController {
        constructor(viewerEl, options) {
            this.viewerEl = viewerEl;
            this.options = { ...DEFAULTS, ...(options || {}) };
            this.scrollEl = viewerEl.querySelector(".js-pdf-scroll");
            this.toolbar = viewerEl.querySelector(".c-pdf-toolbar");
            this.progressEl = viewerEl.querySelector(".js-pdf-progress");
            this.progressLabelEl = viewerEl.querySelector(".js-pdf-progress-label");
            this.currentEl = viewerEl.querySelector(".js-pdf-current");
            this.totalEl = viewerEl.querySelector(".js-pdf-total");
            this.pageInputEl = viewerEl.querySelector(".js-pdf-page-input");
            this.pageTotalEl = viewerEl.querySelector(".js-pdf-page-total");
            this.prevBtn = viewerEl.querySelector(".js-pdf-prev");
            this.nextBtn = viewerEl.querySelector(".js-pdf-next");
            this.zoomInBtn = viewerEl.querySelector(".js-pdf-zoom-in");
            this.zoomOutBtn = viewerEl.querySelector(".js-pdf-zoom-out");
            this.zoomSelectEl = viewerEl.querySelector(".js-pdf-zoom-select");
            this.zoomCustomOption = this.zoomSelectEl
                ? this.zoomSelectEl.querySelector("[data-zoom-custom]")
                : null;
            this.searchInputEl = viewerEl.querySelector(".js-pdf-search-input");
            this.searchPrevBtn = viewerEl.querySelector(".js-pdf-search-prev");
            this.searchNextBtn = viewerEl.querySelector(".js-pdf-search-next");
            this.searchCountEl = viewerEl.querySelector(".js-pdf-search-count");
            this.searchToggleBtn = viewerEl.querySelector(".js-pdf-search-toggle");
            this.searchCloseBtn = viewerEl.querySelector(".js-pdf-search-close");
            this.sidebarToggleBtn = viewerEl.querySelector(".js-pdf-sidebar-toggle");
            this.searchHighlightToggle = viewerEl.querySelector(".js-pdf-search-toggle-highlight");
            this.searchCaseToggle = viewerEl.querySelector(".js-pdf-search-toggle-case");
            this.searchWholeToggle = viewerEl.querySelector(".js-pdf-search-toggle-whole");
            this.brightnessInput = viewerEl.querySelector(".js-pdf-brightness");
            this.brightnessLabel = viewerEl.querySelector(".js-pdf-brightness-label");

            this.sourceUrl = viewerEl.dataset.pdfUrl || "";
            this.contentId = viewerEl.dataset.contentId || "";
            this.progressUrl = viewerEl.dataset.progressUrl || "";
            this.searchUrl = viewerEl.dataset.searchUrl || "";
            this.pageTextUrl = viewerEl.dataset.pageTextUrl || "";
            this.startPage = Math.max(1, Number(viewerEl.dataset.startPage || 1) || 1);
            this.startOffset = Number(viewerEl.dataset.startOffset || 0) || 0;
            this.startDocY = Number(viewerEl.dataset.startDocY || 0) || 0;
            // Always start in automatic (fit-width) zoom; ignore persisted zoom.
            this.startZoom = 0;
            this.maxPageSeen = Math.max(1, Number(viewerEl.dataset.maxPageSeen || this.startPage) || this.startPage);
            this.urlStateEnabled = viewerEl.dataset.urlState !== "false";
            
            this.pagesLayer = null;
            this.pdfjsLib = window.pdfjsLib || null;
            this.docLoader = null;
            this.doc = null;
            this.layout = new LayoutManager(12);
            this.basePageSizes = [];
            this.renderQueue = null;
            this.textContentCache = new Map();
            this.visiblePages = [];
            this.activeMatches = [];
            this.searchMatches = [];
            this.activeMatchIndex = -1;
            this.lastSentAt = Date.now();
            this.maxDocYSeen = 0;
            this.scrollHandler = null;
            this.resizeObserver = null;
            this.bodyObserver = null;
            this.destroyed = false;
            this.searchMode = false;
            this.zoomPreset = "auto";

            this.store = createStore({
                doc: { numPages: 0, pageSizes: [], pageOffsets: [], pageSpacing: 12 },
                view: { scale: 1, zoomMode: "fit-width", viewportW: 0, viewportH: 0, scrollTop: 0 },
                nav: { currentPage: this.startPage, targetPage: this.startPage, urlStateEnabled: this.urlStateEnabled },
                search: { query: "", matchesByPage: [], activeMatchIndex: -1, status: "idle" },
                theme: { mode: "auto", canvasFilter: "auto" },
                progress: { maxDocYSeen: 0, percent: 0 },
            });
        }

        init() {
            if (!this.sourceUrl || !this.scrollEl || !this.pdfjsLib) return this;
            this.docLoader = new DocumentLoader(this.pdfjsLib, this.sourceUrl);
            if (this.pdfjsLib && this.pdfjsLib.GlobalWorkerOptions && !this.pdfjsLib.GlobalWorkerOptions.workerSrc) {
                this.pdfjsLib.GlobalWorkerOptions.workerSrc =
                    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
            }
            this._initToolbar();
            this._setSearchMode(false);
            if (this.brightnessInput) {
                const rawValue = Number(this.brightnessInput.value || 1);
                const forceLight = rawValue <= 0;
                const appliedValue = forceLight ? 1 : rawValue;
                this.viewerEl.classList.toggle("pdf-force-light", forceLight);
                this.viewerEl.style.setProperty("--pdf-brightness", String(appliedValue));
                if (this.brightnessLabel) {
                    this.brightnessLabel.textContent = `${Math.round(rawValue * 100)}%`;
                }
            }
            this._initThemeObserver();
            this._initScroll();
            this._loadDocument();
            if (this.zoomSelectEl && this.zoomCustomOption) {
                this.zoomCustomOption.textContent = "100%";
            }
            return this;
        }

        async _loadDocument() {
            try {
                this.doc = await this.docLoader.load();
            } catch (error) {
                this._setTotalText("error");
                return;
            }
            if (!this.doc) return;

            const numPages = this.doc.numPages || 1;
            this._setTotalText(numPages);

            const firstPage = await this.doc.getPage(1);
            const baseViewport = firstPage.getViewport({ scale: 1 });
            const defaultSizes = Array.from({ length: numPages }, () => ({
                width: baseViewport.width,
                height: baseViewport.height,
            }));

            this.basePageSizes = defaultSizes.slice();
            this.layout.setPageSizes(defaultSizes);
            this.store.setState({
                doc: {
                    numPages,
                    pageSizes: defaultSizes,
                    pageOffsets: this.layout.pageOffsets,
                    pageSpacing: this.layout.pageSpacing,
                },
            });

            this._ensurePagesLayer();
            this._applyInitialScale(baseViewport);
            this._restoreInitialPosition();
            this._scheduleRenderForViewport();
        }

        _setTotalText(value) {
            if (this.totalEl) this.totalEl.textContent = String(value);
            if (this.pageTotalEl) this.pageTotalEl.textContent = `/ ${value}`;
        }

        _ensurePagesLayer() {
            this.scrollEl.innerHTML = "";
            const layer = document.createElement("div");
            layer.className = "c-pdf-pages";
            layer.style.position = "relative";
            layer.style.width = "100%";
            layer.style.height = `${this.layout.getDocHeight()}px`;
            this.scrollEl.appendChild(layer);
            this.pagesLayer = layer;
        }

        _applyInitialScale(baseViewport) {
            const scale = this.startZoom > 0 ? this.startZoom : this._computeFitWidthScale(baseViewport);
            const zoomMode = this.startZoom > 0 ? "custom" : "fit-width";
            this.zoomPreset = zoomMode === "fit-width" ? "auto" : "custom";
            this._setScale(scale, zoomMode);
        }

        _computeFitWidthScale(baseViewport) {
            const availableWidth = Math.max(1, this.scrollEl.clientWidth || baseViewport.width);
            return availableWidth / Math.max(1, baseViewport.width);
        }

        _computeFitPageScale(baseViewport) {
            const availableWidth = Math.max(1, this.scrollEl.clientWidth || baseViewport.width);
            const availableHeight = Math.max(1, this.scrollEl.clientHeight || baseViewport.height);
            const scaleW = availableWidth / Math.max(1, baseViewport.width);
            const scaleH = availableHeight / Math.max(1, baseViewport.height);
            return Math.min(scaleW, scaleH);
        }

        _setScale(scale, zoomMode, skipRelayout) {
            const nextScale = clamp(Number(scale) || 1, 0.3, 4);
            const prevState = this.store.getState();
            this.store.setState({
                view: {
                    ...prevState.view,
                    scale: nextScale,
                    zoomMode,
                    viewportW: this.scrollEl.clientWidth || 0,
                    viewportH: this.scrollEl.clientHeight || 0,
                },
            });
            if (zoomMode === "custom" && this.zoomPreset !== "actual") {
                this.zoomPreset = "custom";
            }
            this._syncZoomSelect(nextScale, zoomMode);
            if (!skipRelayout) {
                this._relayoutForScale();
            }
        }

        _syncZoomSelect(scale, zoomMode) {
            if (!this.zoomSelectEl) return;
            const percent = Math.round(scale * 100);
            if (zoomMode === "fit-page") {
                this.zoomSelectEl.value = "fit-page";
                return;
            }
            if (zoomMode === "fit-width") {
                this.zoomSelectEl.value = this.zoomPreset === "auto" ? "auto" : "fit-width";
                return;
            }
            if (zoomMode === "custom" && percent === 100 && this.zoomPreset === "actual") {
                this.zoomSelectEl.value = "actual";
                return;
            }
            const rawValue = (percent / 100).toString();
            const option = Array.from(this.zoomSelectEl.options).find(
                (opt) => opt.value === rawValue
            );
            if (option) {
                this.zoomSelectEl.value = option.value;
                return;
            }
            if (this.zoomCustomOption) {
                this.zoomCustomOption.textContent = `${percent}%`;
                this.zoomSelectEl.value = "custom";
            }
        }

        _relayoutForScale() {
            const state = this.store.getState();
            const scale = state.view.scale;
            const pageSizes = this.basePageSizes.map((size) => ({
                width: size.width * scale,
                height: size.height * scale,
            }));
            const anchor = this._captureAnchor();
            this.layout.setPageSizes(pageSizes);
            this.store.setState({
                doc: { ...state.doc, pageOffsets: this.layout.pageOffsets, pageSizes },
            });
            if (this.pagesLayer) {
                this.pagesLayer.style.height = `${this.layout.getDocHeight()}px`;
            }
            this._restoreAnchor(anchor);
            this._clearRenderedPages();
            this._scheduleRenderForViewport();
        }

        _captureAnchor() {
            const state = this.store.getState();
            const scrollTop = this.scrollEl.scrollTop || 0;
            const viewportH = this.scrollEl.clientHeight || 1;
            const anchorDocY = scrollTop + viewportH * 0.35;
            const pageIndex = this.layout.findPageAt(anchorDocY);
            const pageOffset = this.layout.getPageOffset(pageIndex);
            const pageHeight = state.doc.pageSizes[pageIndex]?.height || 1;
            const offsetRatio = clamp((anchorDocY - pageOffset) / pageHeight, 0, 1);
            return { pageIndex, offsetRatio };
        }

        _restoreAnchor(anchor) {
            if (!anchor) return;
            const state = this.store.getState();
            const pageOffset = this.layout.getPageOffset(anchor.pageIndex);
            const pageHeight = state.doc.pageSizes[anchor.pageIndex]?.height || 1;
            const viewportH = this.scrollEl.clientHeight || 1;
            const anchorDocY = pageOffset + pageHeight * anchor.offsetRatio;
            const scrollTop = clamp(anchorDocY - viewportH * 0.35, 0, this.layout.getDocHeight());
            this.scrollEl.scrollTop = scrollTop;
        }

        _restoreInitialPosition() {
            const docHeight = this.layout.getDocHeight();
            const viewportH = this.scrollEl.clientHeight || 1;
            if (this.startDocY > 0) {
                const scrollTop = clamp(this.startDocY * docHeight - viewportH * 0.35, 0, docHeight);
                this.scrollEl.scrollTop = scrollTop;
            } else if (this.startPage > 1 || this.startOffset > 0) {
                const pageIndex = clamp(this.startPage - 1, 0, this.layout.pageOffsets.length - 1);
                const pageOffset = this.layout.getPageOffset(pageIndex);
                const pageHeight = this.layout.pageSizes[pageIndex]?.height || 1;
                const anchorDocY = pageOffset + pageHeight * clamp(this.startOffset, 0, 1);
                const scrollTop = clamp(anchorDocY - viewportH * 0.35, 0, docHeight);
                this.scrollEl.scrollTop = scrollTop;
            }
            this._updateNavigationFromScroll();
        }

        _clearRenderedPages() {
            if (this.renderQueue) {
                const rendered = Array.from(this.renderQueue.rendered.keys());
                this.renderQueue.clearRendered(rendered);
            }
        }

        _initScroll() {
            this.scrollHandler = throttle(() => {
                this._updateNavigationFromScroll();
                this._scheduleRenderForViewport();
                this._updateProgress(false);
                this._updateUrlState();
            }, 60);
            this.scrollEl.addEventListener("scroll", this.scrollHandler, { passive: true });
            this.resizeObserver = new ResizeObserver(
                debounce(() => {
                    const zoomMode = this.store.getState().view.zoomMode;
                    if (zoomMode === "fit-width" || zoomMode === "fit-page") {
                        void this._setFitMode(zoomMode);
                    } else {
                        this._relayoutForScale();
                    }
                }, 150)
            );
            this.resizeObserver.observe(this.scrollEl);
        }

        _updateNavigationFromScroll() {
            const state = this.store.getState();
            const scrollTop = this.scrollEl.scrollTop || 0;
            const viewportH = this.scrollEl.clientHeight || 1;
            const anchorDocY = scrollTop + viewportH * 0.35;
            const pageIndex = this.layout.findPageAt(anchorDocY);
            const currentPage = pageIndex + 1;
            if (this.currentEl) this.currentEl.textContent = String(currentPage);
            if (this.pageInputEl) this.pageInputEl.value = String(currentPage);
            this.store.setState({
                view: { ...state.view, scrollTop, viewportW: this.scrollEl.clientWidth || 0, viewportH },
                nav: { ...state.nav, currentPage },
            });
        }

        _scheduleRenderForViewport() {
            if (!this.doc) return;
            if (!this.renderQueue) {
                this.renderQueue = new RenderQueue((pageNumber, generation) => this._renderPage(pageNumber, generation), this.options);
            }
            const state = this.store.getState();
            const scrollTop = this.scrollEl.scrollTop || 0;
            const viewportH = this.scrollEl.clientHeight || 1;
            const startY = Math.max(0, scrollTop - viewportH * 0.5);
            const endY = scrollTop + viewportH * 1.5;
            const startPage = this.layout.findPageAt(startY);
            const endPage = this.layout.findPageAt(endY);
            const windowStart = clamp(startPage - this.options.prefetchPadding, 0, state.doc.numPages - 1);
            const windowEnd = clamp(endPage + this.options.prefetchPadding, 0, state.doc.numPages - 1);
            const windowPages = [];
            for (let i = windowStart; i <= windowEnd; i += 1) {
                windowPages.push(i + 1);
            }
            this._ensureWindowPages(windowStart, windowEnd);
            this.renderQueue.evict(windowPages);
            windowPages.forEach((pageNumber) => {
                const priority = pageNumber >= startPage + 1 && pageNumber <= endPage + 1 ? 2 : 1;
                this.renderQueue.schedule(pageNumber, priority);
            });
        }

        _ensureWindowPages(windowStart, windowEnd) {
            if (!this.pagesLayer) return;
            const existing = Array.from(this.pagesLayer.querySelectorAll(".js-pdf-page"));
            const keep = new Set();
            for (let i = windowStart; i <= windowEnd; i += 1) {
                const pageNumber = i + 1;
                keep.add(pageNumber);
                const existingNode = this.pagesLayer.querySelector(`[data-page-number="${pageNumber}"]`);
                if (!existingNode) {
                    const node = this._createPageNode(pageNumber);
                    this.pagesLayer.appendChild(node);
                } else {
                    const pageIndex = pageNumber - 1;
                    const size = this.layout.pageSizes[pageIndex] || { height: 0 };
                    existingNode.style.top = `${this.layout.getPageOffset(pageIndex)}px`;
                    existingNode.style.height = `${size.height}px`;
                }
            }
            existing.forEach((node) => {
                const pageNumber = Number(node.dataset.pageNumber || 0);
                if (!keep.has(pageNumber)) {
                    node.remove();
                }
            });
        }

        _createPageNode(pageNumber) {
            const pageIndex = pageNumber - 1;
            const size = this.layout.pageSizes[pageIndex] || { width: 0, height: 0 };
            const pageOffset = this.layout.getPageOffset(pageIndex);
            const node = document.createElement("div");
            node.className = "c-pdf-page js-pdf-page";
            node.dataset.pageNumber = String(pageNumber);
            node.style.position = "absolute";
            node.style.left = "0";
            node.style.top = `${pageOffset}px`;
            node.style.width = "100%";
            node.style.height = `${size.height}px`;

            const canvasEl = document.createElement("canvas");
            canvasEl.className = "c-pdf-page__canvas js-pdf-canvas";
            canvasEl.setAttribute("aria-label", `PDF page ${pageNumber}`);
            node.appendChild(canvasEl);

            const textLayerEl = document.createElement("div");
            textLayerEl.className = "c-pdf-page__text-layer js-pdf-text-layer";
            node.appendChild(textLayerEl);

            const highlightLayer = document.createElement("div");
            highlightLayer.className = "c-pdf-page__highlights js-pdf-highlights";
            node.appendChild(highlightLayer);

            return node;
        }

        async _renderPage(pageNumber, generation) {
            if (!this.doc) return;
            const state = this.store.getState();
            const scale = state.view.scale;
            const pageIndex = pageNumber - 1;
            const node = this.pagesLayer.querySelector(`[data-page-number="${pageNumber}"]`);
            if (!node) return;
            const canvasEl = node.querySelector(".js-pdf-canvas");
            const textLayerEl = node.querySelector(".js-pdf-text-layer");
            if (!canvasEl) return;

            const page = await this.doc.getPage(pageNumber);
            const baseViewport = page.getViewport({ scale: 1 });
            const viewport = page.getViewport({ scale });
            const dpr = clamp(window.devicePixelRatio || 1, 1, this.options.maxDpr);

            canvasEl.width = Math.floor(viewport.width * dpr);
            canvasEl.height = Math.floor(viewport.height * dpr);
            canvasEl.style.width = `${Math.floor(viewport.width)}px`;
            canvasEl.style.height = `${Math.floor(viewport.height)}px`;
            canvasEl.style.pointerEvents = "none";

            const ctx = canvasEl.getContext("2d");
            if (!ctx) return;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

            await page.render({ canvasContext: ctx, viewport }).promise;

            await this._renderTextLayer(page, viewport, textLayerEl);

            if (generation !== this.renderQueue.generation) return;
            node.style.height = `${Math.floor(viewport.height)}px`;
            this.basePageSizes[pageIndex] = { width: baseViewport.width, height: baseViewport.height };
            this.layout.updatePageSize(pageIndex, { width: baseViewport.width * scale, height: baseViewport.height * scale });
            if (this.pagesLayer) {
                this.pagesLayer.style.height = `${this.layout.getDocHeight()}px`;
            }
            this.store.setState({
                doc: { ...state.doc, pageOffsets: this.layout.pageOffsets, pageSizes: this.layout.pageSizes },
            });
            node.style.top = `${this.layout.getPageOffset(pageIndex)}px`;
            this.renderQueue.markRendered(pageNumber, {
                dispose: () => {
                    if (canvasEl) {
                        canvasEl.width = 0;
                        canvasEl.height = 0;
                    }
                    if (textLayerEl) {
                        textLayerEl.textContent = "";
                    }
                    const highlightLayer = node.querySelector(".js-pdf-highlights");
                    if (highlightLayer) highlightLayer.textContent = "";
                },
            });
            this._applySearchHighlightsToPage(pageNumber);
        }

        async _renderTextLayer(page, viewport, textLayerEl) {
            if (!textLayerEl) return;
            textLayerEl.textContent = "";
            textLayerEl.style.width = `${Math.floor(viewport.width)}px`;
            textLayerEl.style.height = `${Math.floor(viewport.height)}px`;
            const textContent = await page.getTextContent();
            this.textContentCache.set(page.pageNumber, textContent);

            if (typeof window.pdfjsLib.TextLayer === "function") {
                try {
                    const textLayer = new window.pdfjsLib.TextLayer({
                        textContentSource: textContent,
                        container: textLayerEl,
                        viewport,
                    });
                    const maybePromise = textLayer.render();
                    if (maybePromise && typeof maybePromise.then === "function") {
                        await maybePromise;
                    }
                    return;
                } catch (error) {
                    // fallback below
                }
            }
            if (typeof window.pdfjsLib.renderTextLayer === "function") {
                try {
                    const task = window.pdfjsLib.renderTextLayer({
                        container: textLayerEl,
                        viewport,
                        enhanceTextSelection: true,
                        textContent,
                        textContentSource: textContent,
                    });
                    if (task && task.promise) {
                        await task.promise;
                    } else if (task && typeof task.then === "function") {
                        await task;
                    }
                    return;
                } catch (error) {
                    // fallback below
                }
            }
            this._renderTextLayerFallback(textLayerEl, textContent, viewport);
        }

        _renderTextLayerFallback(textLayerEl, textContent, viewport) {
            const util = window.pdfjsLib && window.pdfjsLib.Util;
            if (!util || !textLayerEl || !textContent || !Array.isArray(textContent.items)) return;
            const styles = textContent.styles || {};
            const fragment = document.createDocumentFragment();
            textContent.items.forEach((item) => {
                if (!item || typeof item.str !== "string" || !item.str.length) return;
                const style = styles[item.fontName] || {};
                const tx = util.transform(
                    util.transform(viewport.transform, item.transform),
                    [1, 0, 0, -1, 0, 0]
                );
                const fontHeight = Math.hypot(tx[2], tx[3]);
                if (!Number.isFinite(fontHeight) || fontHeight <= 0) return;
                let fontAscent = fontHeight;
                if (Number.isFinite(style.ascent)) {
                    fontAscent = style.ascent * fontHeight;
                } else if (Number.isFinite(style.descent)) {
                    fontAscent = (1 + style.descent) * fontHeight;
                }
                const span = document.createElement("span");
                span.textContent = item.str;
                span.dir = item.dir || "ltr";
                span.style.position = "absolute";
                span.style.whiteSpace = "pre";
                span.style.transformOrigin = "0 0";
                span.style.color = "rgba(0, 0, 0, 0.001)";
                span.style.webkitTextFillColor = "rgba(0, 0, 0, 0.001)";
                span.style.cursor = "text";
                span.style.userSelect = "text";
                span.style.webkitUserSelect = "text";
                span.style.left = `${tx[4]}px`;
                span.style.top = `${tx[5] - fontAscent}px`;
                span.style.fontSize = `${fontHeight}px`;
                span.style.fontFamily = style.fontFamily || "sans-serif";
                fragment.appendChild(span);
            });
            textLayerEl.appendChild(fragment);
        }

        _initToolbar() {
            if (this.sidebarToggleBtn) {
                this.sidebarToggleBtn.addEventListener("click", () => {
                    if (typeof window.toggleModulesCollapsed === "function") {
                        window.toggleModulesCollapsed();
                        return;
                    }
                    const workspaceEl = document.querySelector(".course-workspace");
                    const sidebarEl = document.getElementById("module-sidebar");
                    if (!workspaceEl || !sidebarEl) return;
                    const isCollapsed = document.body.classList.contains("modules-sidebar-collapsed");
                    const nextCollapsed = !isCollapsed;
                    workspaceEl.classList.toggle("modules-collapsed", nextCollapsed);
                    document.body.classList.toggle("modules-sidebar-collapsed", nextCollapsed);
                    sidebarEl.setAttribute("aria-hidden", String(nextCollapsed));
                    const unhideBtn = document.getElementById("modules-unhide");
                    if (unhideBtn) {
                        unhideBtn.setAttribute("aria-expanded", String(!nextCollapsed));
                    }
                    try {
                        localStorage.setItem(
                            "modules_sidebar_state",
                            nextCollapsed ? "collapsed" : "expanded"
                        );
                    } catch (error) {
                        // ignore storage failures
                    }
                });
            }
            if (this.searchToggleBtn) {
                this.searchToggleBtn.addEventListener("click", () => this._setSearchMode(true));
            }
            if (this.searchCloseBtn) {
                this.searchCloseBtn.addEventListener("click", () => this._setSearchMode(false));
            }
            if (this.prevBtn) {
                this.prevBtn.addEventListener("click", () => this._jumpToPage(this.store.getState().nav.currentPage - 1));
            }
            if (this.nextBtn) {
                this.nextBtn.addEventListener("click", () => this._jumpToPage(this.store.getState().nav.currentPage + 1));
            }
            if (this.pageInputEl) {
                this.pageInputEl.addEventListener("change", () => {
                    const page = Number(this.pageInputEl.value || 1);
                    this._jumpToPage(page);
                });
            }
            if (this.zoomInBtn) {
                this.zoomInBtn.addEventListener("click", () => this._adjustZoom(0.1));
            }
            if (this.zoomOutBtn) {
                this.zoomOutBtn.addEventListener("click", () => this._adjustZoom(-0.1));
            }
            if (this.zoomSelectEl) {
                this.zoomSelectEl.addEventListener("change", () => {
                    const value = this.zoomSelectEl.value;
                    this.zoomPreset = value;
                    if (value === "auto") {
                        this._setFitMode("fit-width");
                        return;
                    }
                    if (value === "actual") {
                        this._setScale(1, "custom");
                        return;
                    }
                    if (value === "fit-page") {
                        this._setFitMode("fit-page");
                        return;
                    }
                    if (value === "fit-width") {
                        this._setFitMode("fit-width");
                        return;
                    }
                    const parsed = Number(value);
                    if (Number.isFinite(parsed) && parsed > 0) {
                        this._setScale(parsed, "custom");
                    }
                });
            }
            if (this.searchInputEl) {
                const runSearch = debounce(() => {
                    this._search(this.searchInputEl.value || "");
                }, 280);
                this.searchInputEl.addEventListener("input", runSearch);
                this.searchInputEl.addEventListener("keydown", (event) => {
                    if (event.key === "Enter") {
                        event.preventDefault();
                        this._search(this.searchInputEl.value || "");
                    }
                    if (event.key === "Escape") {
                        event.preventDefault();
                        this._setSearchMode(false);
                    }
                });
                this.searchInputEl.addEventListener("focus", () => this._setSearchMode(true));
            }
            if (this.searchPrevBtn) {
                this.searchPrevBtn.addEventListener("click", () => this._stepMatch(-1));
            }
            if (this.searchNextBtn) {
                this.searchNextBtn.addEventListener("click", () => this._stepMatch(1));
            }
            if (this.searchHighlightToggle) {
                this.searchHighlightToggle.addEventListener("click", () => {
                    const pressed = this.searchHighlightToggle.getAttribute("aria-pressed") !== "true";
                    this.searchHighlightToggle.setAttribute("aria-pressed", String(pressed));
                });
            }
            if (this.searchCaseToggle) {
                this.searchCaseToggle.addEventListener("click", () => {
                    const pressed = this.searchCaseToggle.getAttribute("aria-pressed") !== "true";
                    this.searchCaseToggle.setAttribute("aria-pressed", String(pressed));
                });
            }
            if (this.searchWholeToggle) {
                this.searchWholeToggle.addEventListener("click", () => {
                    const pressed = this.searchWholeToggle.getAttribute("aria-pressed") !== "true";
                    this.searchWholeToggle.setAttribute("aria-pressed", String(pressed));
                });
            }
            if (this.brightnessInput) {
                this.brightnessInput.addEventListener("input", () => {
                    const rawValue = Number(this.brightnessInput.value || 1);
                    const percent = Math.round(rawValue * 100);
                    const forceLight = rawValue <= 0;
                    const appliedValue = forceLight ? 1 : rawValue;
                    this.viewerEl.classList.toggle("pdf-force-light", forceLight);
                    this.viewerEl.style.setProperty("--pdf-brightness", String(appliedValue));
                    if (this.brightnessLabel) {
                        this.brightnessLabel.textContent = `${percent}%`;
                    }
                });
            }
        }

        _setSearchMode(enabled) {
            this.searchMode = Boolean(enabled);
            if (this.toolbar) {
                this.toolbar.classList.toggle("is-search-active", this.searchMode);
                this.toolbar.dataset.toolbarState = this.searchMode ? "search" : "default";
            }
            if (this.searchMode && this.searchInputEl) {
                this.searchInputEl.focus({ preventScroll: true });
                this.searchInputEl.select();
            }
        }

        _initThemeObserver() {
            const body = document.body;
            if (!body || typeof MutationObserver !== "function") return;
            this.bodyObserver = new MutationObserver(() => {
                this._syncThemeWithBody();
            });
            this.bodyObserver.observe(body, { attributes: true, attributeFilter: ["class"] });
            this._syncThemeWithBody();
        }

        _syncThemeWithBody() {
            const body = document.body;
            const darkMode = body && body.classList.contains("theme-dark");
            this.viewerEl.classList.toggle("pdf-theme-dark", darkMode);
        }

        _adjustZoom(delta) {
            const state = this.store.getState();
            this.zoomPreset = "custom";
            this._setScale(state.view.scale + delta, "custom");
        }

        async _setFitMode(mode) {
            if (!this.doc) return;
            const firstPage = await this.doc.getPage(1);
            const baseViewport = firstPage.getViewport({ scale: 1 });
            const scale = mode === "fit-page"
                ? this._computeFitPageScale(baseViewport)
                : this._computeFitWidthScale(baseViewport);
            if (mode === "fit-width" && this.zoomPreset !== "auto") {
                this.zoomPreset = "fit-width";
            }
            if (mode === "fit-page") {
                this.zoomPreset = "fit-page";
            }
            this._setScale(scale, mode);
        }

        _jumpToPage(page) {
            const state = this.store.getState();
            const target = clamp(Number(page) || 1, 1, state.doc.numPages || 1);
            const pageIndex = target - 1;
            const offset = this.layout.getPageOffset(pageIndex);
            const viewportH = this.scrollEl.clientHeight || 1;
            const scrollTop = clamp(offset - viewportH * 0.1, 0, this.layout.getDocHeight());
            this.scrollEl.scrollTo({ top: scrollTop, behavior: "smooth" });
        }

        async _search(query) {
            const normalized = String(query || "").trim();
            if (!normalized) {
                this.searchMatches = [];
                this.activeMatchIndex = -1;
                this._updateSearchUI();
                this._clearSearchHighlights();
                return;
            }
            if (!this.searchUrl) return;
            try {
                const url = new URL(this.searchUrl, window.location.origin);
                url.searchParams.set("q", normalized);
                const response = await fetch(url.toString(), { credentials: "same-origin" });
                const data = response.ok ? await response.json() : null;
                this.searchMatches = Array.isArray(data && data.matches) ? data.matches : [];
                this.activeMatchIndex = this.searchMatches.length ? 0 : -1;
                this._updateSearchUI();
                if (this.searchMatches.length) {
                    this._jumpToMatch(this.activeMatchIndex);
                }
            } catch (error) {
                // ignore
            }
        }

        _updateSearchUI() {
            if (!this.searchCountEl) return;
            if (!this.searchMatches.length) {
                this.searchCountEl.textContent = "";
                return;
            }
            this.searchCountEl.textContent = `${this.activeMatchIndex + 1} / ${this.searchMatches.length}`;
        }

        _stepMatch(direction) {
            if (!this.searchMatches.length) return;
            const total = this.searchMatches.length;
            let next = this.activeMatchIndex + direction;
            if (next < 0) next = total - 1;
            if (next >= total) next = 0;
            this.activeMatchIndex = next;
            this._updateSearchUI();
            this._jumpToMatch(next);
        }

        _jumpToMatch(index) {
            const match = this.searchMatches[index];
            if (!match) return;
            const page = Number(match.page || 1);
            this._jumpToPage(page);
            window.setTimeout(() => {
                this._applySearchHighlightsToPage(page);
                const node = this.pagesLayer.querySelector(`[data-page-number="${page}"]`);
                const highlight = node && node.querySelector(".c-pdf-highlight.is-active");
                if (highlight) {
                    highlight.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }, 300);
        }

        _clearSearchHighlights() {
            if (!this.pagesLayer) return;
            this.pagesLayer.querySelectorAll(".c-pdf-highlight").forEach((el) => el.remove());
            this.pagesLayer.querySelectorAll(".c-pdf-hit").forEach((el) => el.classList.remove("c-pdf-hit"));
        }

        _applySearchHighlightsToPage(pageNumber) {
            if (!this.searchInputEl || !this.searchInputEl.value) return;
            const query = this.searchInputEl.value.trim().toLowerCase();
            if (!query) return;
            const pageNode = this.pagesLayer.querySelector(`[data-page-number="${pageNumber}"]`);
            if (!pageNode) return;
            const highlightLayer = pageNode.querySelector(".js-pdf-highlights");
            if (!highlightLayer) return;
            highlightLayer.textContent = "";
            const spans = Array.from(pageNode.querySelectorAll(".js-pdf-text-layer span"));
            spans.forEach((span) => {
                const text = (span.textContent || "").toLowerCase();
                if (text.includes(query)) {
                    span.classList.add("c-pdf-hit");
                    const rect = span.getBoundingClientRect();
                    const parentRect = pageNode.getBoundingClientRect();
                    const highlight = document.createElement("div");
                    highlight.className = "c-pdf-highlight";
                    highlight.style.left = `${rect.left - parentRect.left}px`;
                    highlight.style.top = `${rect.top - parentRect.top}px`;
                    highlight.style.width = `${rect.width}px`;
                    highlight.style.height = `${rect.height}px`;
                    highlightLayer.appendChild(highlight);
                }
            });
            const highlights = Array.from(highlightLayer.querySelectorAll(".c-pdf-highlight"));
            if (highlights.length) {
                highlights[0].classList.add("is-active");
            }
        }

        _updateProgress(force) {
            const state = this.store.getState();
            const totalPages = state.doc.numPages || 1;
            const currentPage = state.nav.currentPage || 1;
            this.maxPageSeen = Math.max(this.maxPageSeen, currentPage);
            const progressPercent = clamp((this.maxPageSeen / totalPages) * 100, 0, 100);
            if (this.progressEl) {
                this.progressEl.style.setProperty("--pdf-progress", `${Math.round(progressPercent)}%`);
                this.progressEl.setAttribute("aria-valuenow", String(Math.round(progressPercent)));
                this.progressEl.classList.toggle("is-complete", progressPercent >= 100);
            }
            if (this.progressLabelEl) {
                this.progressLabelEl.textContent = progressPercent >= 100 ? "\u2714" : `${Math.round(progressPercent)}%`;
            }
            this.store.setState({
                progress: { maxDocYSeen: this.maxDocYSeen, percent: progressPercent },
            });
            this._sendProgress(force);
        }

        _sendProgress(force) {
            if (!this.progressUrl) return;
            const now = Date.now();
            const secondsDelta = Math.max(0, Math.floor((now - this.lastSentAt) / 1000));
            if (!force && secondsDelta < 5) return;
            this.lastSentAt = now;
            const state = this.store.getState();
            const docHeight = this.layout.getDocHeight();
            const scrollTop = this.scrollEl.scrollTop || 0;
            const viewportH = this.scrollEl.clientHeight || 1;
            const anchorDocY = scrollTop + viewportH * 0.35;
            const pageIndex = this.layout.findPageAt(anchorDocY);
            const pageOffset = this.layout.getPageOffset(pageIndex);
            const pageHeight = state.doc.pageSizes[pageIndex]?.height || 1;
            const pageOffsetRatio = clamp((anchorDocY - pageOffset) / pageHeight, 0, 1);
            const docYRatio = clamp(anchorDocY / docHeight, 0, 1);

            const payload = {
                kind: "pdf",
                current_page: pageIndex + 1,
                total_pages: state.doc.numPages,
                max_page_seen: Math.max(this.maxPageSeen, pageIndex + 1),
                doc_progress: state.progress.percent,
                doc_y_ratio: docYRatio,
                page_offset_ratio: pageOffsetRatio,
                zoom: state.view.scale,
                viewport_w: state.view.viewportW,
                viewport_h: state.view.viewportH,
                seconds_delta: secondsDelta,
            };
            this.maxPageSeen = Math.max(this.maxPageSeen, pageIndex + 1);

            const send = this.options.postProgress
                ? this.options.postProgress(this.progressUrl, payload, { keepalive: force })
                : fetch(this.progressUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": this._getCookie("csrftoken"),
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    credentials: "same-origin",
                    keepalive: Boolean(force),
                    body: JSON.stringify(payload),
                }).then((response) => (response.ok ? response.json().catch(() => null) : null));

            Promise.resolve(send).then((data) => {
                if (data && typeof this.options.onProgressPayload === "function") {
                    this.options.onProgressPayload(data);
                }
            });
        }

        _getCookie(name) {
            const cookies = document.cookie ? document.cookie.split("; ") : [];
            for (const c of cookies) {
                const [key, ...rest] = c.split("=");
                if (key === name) return decodeURIComponent(rest.join("="));
            }
            return "";
        }

        _updateUrlState() {
            if (!this.options.urlState || !this.urlStateEnabled) return;
            const state = this.store.getState();
            const params = new URLSearchParams(window.location.search);
            params.set("content_id", String(this.contentId || ""));
            params.set("page", String(state.nav.currentPage));
            params.set("zoom", state.view.scale.toFixed(2));
            if (this.searchInputEl && this.searchInputEl.value) {
                params.set("q", this.searchInputEl.value.trim());
                params.set("match", String(this.activeMatchIndex + 1));
            } else {
                params.delete("q");
                params.delete("match");
            }
            const url = `${window.location.pathname}?${params.toString()}`;
            this._throttledReplaceState(url);
        }

        _throttledReplaceState(url) {
            if (!this._replaceStateThrottled) {
                this._replaceStateThrottled = throttle((nextUrl) => {
                    window.history.replaceState({}, "", nextUrl);
                }, 200);
            }
            this._replaceStateThrottled(url);
        }

        refreshLayout() {
            const zoomMode = this.store.getState().view.zoomMode;
            if (zoomMode === "fit-width" || zoomMode === "fit-page") {
                void this._setFitMode(zoomMode);
            } else {
                this._relayoutForScale();
            }
        }

        setAutoZoom() {
            this.zoomPreset = "auto";
            this._setFitMode("fit-width");
        }

        flushProgress(force) {
            this._updateProgress(Boolean(force));
        }

        destroy() {
            this.destroyed = true;
            if (this.scrollHandler) {
                this.scrollEl.removeEventListener("scroll", this.scrollHandler);
            }
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
            }
            if (this.bodyObserver) {
                this.bodyObserver.disconnect();
            }
            if (this.renderQueue) {
                this.renderQueue.reset();
            }
            if (this.docLoader) {
                this.docLoader.destroy();
            }
            this.textContentCache.clear();
        }
    }

    function initAll(options = {}) {
        const viewerEls = Array.from(document.querySelectorAll(".js-pdf-viewer"));
        viewerEls.forEach((el) => {
            if (instances.has(el)) return;
            const controller = new PdfViewerController(el, options);
            controller.init();
            instances.set(el, controller);
        });
        return instances;
    }

    function refreshAll() {
        instances.forEach((controller) => controller.refreshLayout());
    }

    function setAutoZoomAll() {
        instances.forEach((controller) => controller.setAutoZoom());
    }

    function flushAll() {
        instances.forEach((controller) => controller.flushProgress(true));
    }

    function destroyAll() {
        instances.forEach((controller) => controller.destroy());
        instances.clear();
    }

    window.PdfViewer = {
        initAll,
        refreshAll,
        setAutoZoomAll,
        flushAll,
        destroyAll,
    };
})();
