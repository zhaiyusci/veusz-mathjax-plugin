// mathjax_bridge.cpp - Veusz 的 MathJax 4 SVG 渲染桥接
//
// Copyright 2026 Yu Zhai
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// 设计:
//   - 内部用 QuickJS 跑 MathJax v4 bundle,渲染 LaTeX -> SVG
//   - 输出包含 baseline (vertical-align) 信息
//   - 不依赖 Qt,只依赖 QuickJS 动态库 (qjs.dll / libqjs.so)
//   - API 与 microtex_bridge 对齐,但额外返回 baseline
//
// 构建依赖:
//   - QuickJS 动态库 (qjs.dll, 由 src/build-quickjs-windows.cmd 用上游的
//     -DBUILD_SHARED_LIBS=ON 构建) 及其 import library
//   - quickjs.h, quickjs-libc.h
//   - mathjax_bundle.js (运行时加载)
//
// 注意: 因为链接的是 QuickJS 的 import library, 编译时必须定义
// USING_QJS_SHARED -- quickjs.h 用它把 API 声明为 __declspec(dllimport)。
// 上游自己的 CMake 构建会自动加 (作为 qjs target 的 PUBLIC 定义),
// 裸 cl 调用要手动传。

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <string>
#include <vector>

#ifdef _WIN32
#  define WIN32_LEAN_AND_MEAN
#  define NOMINMAX
#  include <windows.h>
#endif

// QuickJS headers (路径由 CMake 配置)
#include "quickjs.h"
#include "quickjs-libc.h"

// ============================================================
// 内部状态
//
// 这个 DLL 是**通用 JS 宿主**,不是"MathJax 渲染器":它 eval 一个 bundle,
// 然后调用 bundle 暴露的全局函数。一个 DLL 里可以同时承载多个 bundle
// (每个 bundle 一个独立 QuickJS 运行时),这样 MathJax 与 KaTeX 之类可以
// 并存;句柄是 g_states 的 1-based 下标。调用方负责加锁。
//
// mathjax_* 这套旧 API 仍然操作"第一个初始化的 bundle"。
// ============================================================

namespace {

struct JsState {
    JSRuntime* rt = nullptr;
    JSContext* ctx = nullptr;
    bool initialized = false;
    std::string path;
};

std::vector<JsState*> g_states;
JsState* g_primary = nullptr;

JsState* primary_state() {
    if (!g_primary) {
        g_primary = new JsState();
        g_states.push_back(g_primary);
    }
    return g_primary;
}

JsState* state_for_handle(int handle) {
    if (handle <= 0 || static_cast<size_t>(handle) > g_states.size()) {
        return nullptr;
    }
    return g_states[handle - 1];
}


// bundle 路径,可通过环境变量 VEUSZ_MATHJAX_BUNDLE 覆盖
const char* bundle_path() {
    const char* env = getenv("VEUSZ_MATHJAX_BUNDLE");
    if (env && *env) return env;
    // 默认在可执行文件目录或包数据目录下查找
    return nullptr;
}

// 读取文件内容到 malloc 缓冲区
char* read_file(const char* path, int* size_out) {
    FILE* f = fopen(path, "rb");
    if (!f) return nullptr;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    char* buf = (char*)malloc(size + 1);
    if (!buf) { fclose(f); return nullptr; }
    size_t n = fread(buf, 1, size, f);
    fclose(f);
    buf[n] = '\0';
    if (size_out) *size_out = (int)n;
    return buf;
}

char* dup_string(const char* s) {
    size_t n = strlen(s);
    char* p = (char*)malloc(n + 1);
    if (!p) return nullptr;
    memcpy(p, s, n + 1);
    return p;
}

// QuickJS anchors its JS stack limit to the thread that CREATED the runtime:
// rt->stack_top is captured in JS_NewRuntime(), and js_check_stack_overflow()
// then tests sp < rt->stack_top - rt->stack_size.
//
// Veusz renders TeX from two different threads: interactive painting runs on
// a QThreadPool worker, while exports and non-interactive previews run on the
// main thread.  Reserved thread stacks here are only ~2.9 MiB, yet two thread
// stacks can be placed more than 8 MiB apart, so calling the runtime from the
// thread that happens to sit lower failed immediately with
//
//     RangeError: Maximum call stack size exceeded
//
// for perfectly ordinary formulas.  Re-anchor the stack top on every call and
// size the JS budget from what the calling thread actually has left, so deep
// recursion yields a catchable JS error instead of running off the real stack.
void refresh_stack_budget(JSRuntime* rt) {
    if (!rt) return;
    JS_UpdateStackTop(rt);

    size_t avail = 0;
#ifdef _WIN32
    typedef BOOL (WINAPI *GetStackLimitsFn)(PULONG_PTR, PULONG_PTR);
    static GetStackLimitsFn get_limits = nullptr;
    static bool looked_up = false;
    if (!looked_up) {
        looked_up = true;
        HMODULE k32 = GetModuleHandleA("kernel32.dll");
        if (k32) {
            get_limits = (GetStackLimitsFn)(
                void*)GetProcAddress(k32, "GetCurrentThreadStackLimits");
        }
    }
    if (get_limits) {
        ULONG_PTR low = 0, high = 0;
        get_limits(&low, &high);
        volatile char probe = 0;
        ULONG_PTR sp = (ULONG_PTR)&probe;
        if (low != 0 && sp > low) {
            avail = (size_t)(sp - low);
        }
    }
#endif

    size_t budget = 0;
    if (avail > 256 * 1024) {
        // All but a small margin: QuickJS accounts its JS stack in native
        // bytes, so this stays inside the thread's real stack while giving
        // deeply nested formulas as much room as the thread can offer.
        budget = avail - 128 * 1024;
    }
    if (budget < 256 * 1024) budget = 256 * 1024;
    if (budget > 8 * 1024 * 1024) budget = 8 * 1024 * 1024;
    JS_SetMaxStackSize(rt, budget);
}

// Object.hasOwn polyfill (QuickJS 不支持 ES2022)
const char* POLYFILL =
"if (!Object.hasOwn) {"
"  Object.hasOwn = function(obj, prop) {"
"    return Object.prototype.hasOwnProperty.call(obj, prop);"
"  };"
"}\n";

// 从 SVG 字符串里解析 width / height / vertical-align
// 例: <svg style="vertical-align: -0.566ex;" width="1.733ex" height="2.262ex" ...>
void parse_svg_metrics(const char* svg,
                       char* width_out, int width_sz,
                       char* height_out, int height_sz,
                       char* valign_out, int valign_sz) {
    if (width_out) width_out[0] = '\0';
    if (height_out) height_out[0] = '\0';
    if (valign_out) valign_out[0] = '\0';
    if (!svg) return;

    // width="..."
    if (width_out && width_sz > 0) {
        const char* kw = "width=\"";
        const char* found = strstr(svg, kw);
        if (found) {
            const char* start = found + strlen(kw);
            const char* end = strchr(start, '"');
            if (end && end - start < width_sz - 1) {
                int len = end - start;
                memcpy(width_out, start, len);
                width_out[len] = '\0';
            }
        }
    }
    // height="..."
    if (height_out && height_sz > 0) {
        const char* kw = "height=\"";
        const char* found = strstr(svg, kw);
        if (found) {
            const char* start = found + strlen(kw);
            const char* end = strchr(start, '"');
            if (end && end - start < height_sz - 1) {
                int len = end - start;
                memcpy(height_out, start, len);
                height_out[len] = '\0';
            }
        }
    }
    // vertical-align: -X.XXXex
    if (valign_out && valign_sz > 0) {
        const char* kw = "vertical-align: ";
        const char* found = strstr(svg, kw);
        if (found) {
            const char* start = found + strlen(kw);
            const char* end = start;
            while (*end && *end != ';' && *end != '"' && *end != ' ') end++;
            if (end - start < valign_sz - 1) {
                int len = end - start;
                memcpy(valign_out, start, len);
                valign_out[len] = '\0';
            }
        }
    }
}

// MathJax 的 SVG 以 ex 为单位输出长度,而这里的 1ex 指的是**数学字体的
// x-height**。字体包把该值放在 defaultParams.x_height 里:
//   @mathjax/mathjax-newcm-font  →  0.442 em
// 早先这里硬编码 1ex = text_size/2 (即 0.5em),于是 MathJax 排出来的公式
// 比请求的字号大约 0.5/0.442 = 13%,也就比 MicroTeX / 系统 LaTeX 明显偏大
// (见 tools/probe_engine_scale.py 的实测:同 12pt 下 x 高 6.29pt vs 5.17pt)。
// 换字体时用 VEUSZ_MATHJAX_EXHEIGHT 覆盖(fontData 的 x_height)。
static float g_ex_height = 0.442f;

void read_ex_height_env() {
    const char* env = getenv("VEUSZ_MATHJAX_EXHEIGHT");
    if (!env || !*env) return;
    float val = static_cast<float>(atof(env));
    if (val > 0.05f && val <= 1.0f) {
        g_ex_height = val;
    }
}

// 把 SVG 里的长度按字体大小换算:
//   "ex" 或无单位  -> val * size * g_ex_height   (size 的单位就是结果的单位)
//   其它单位       -> 原样返回,视为已经是目标单位
// 当前调用方只传 ex 字符串(width/height/vertical-align)并要 pt,所以传 pt。
// (函数名以前叫 ex_to_px,但它并不总返回像素,容易误用,故改名。)
float ex_to_size(const char* ex_str, float size) {
    if (!ex_str || !*ex_str) return 0.0f;
    // 手动解析: 浮点数 + 可选单位
    float val = 0.0f;
    const char* p = ex_str;
    bool neg = false;
    if (*p == '-') { neg = true; p++; }
    else if (*p == '+') { p++; }
    // 整数部分
    while (*p >= '0' && *p <= '9') {
        val = val * 10.0f + (*p - '0');
        p++;
    }
    // 小数部分
    if (*p == '.') {
        p++;
        float frac = 0.1f;
        while (*p >= '0' && *p <= '9') {
            val += (*p - '0') * frac;
            frac *= 0.1f;
            p++;
        }
    }
    if (neg) val = -val;
    // 单位
    if (strncmp(p, "ex", 2) == 0) {
        return val * size * g_ex_height;
    }
    if (strncmp(p, "pt", 2) == 0) {
        return val;
    }
    if (strncmp(p, "px", 2) == 0) {
        return val;
    }
    // 无单位,按 ex 处理
    return val * size * g_ex_height;
}

// 把 SVG 根元素的 width/height/style 从 ex 单位转成 pt 单位。
// 只处理 SVG 根标签里的属性,不处理 viewBox 或子元素(它们是设计单位)。
// 使用 strtof 而非 sscanf,因为 sscanf 的 %f 会把 "3.466ex" 里的 'e'
// 误当成指数符号,导致整个解析失败。
void rewrite_svg_units(std::string& svg, float text_size_pt) {
    if (text_size_pt <= 0) return;
    float ex_to_pt = text_size_pt * g_ex_height;

    size_t svg_start = svg.find("<svg");
    size_t svg_end = svg.find(">", svg_start);
    if (svg_start == std::string::npos || svg_end == std::string::npos) return;

    std::string svg_tag = svg.substr(svg_start, svg_end - svg_start + 1);

    const char* attrs[] = {"width=\"", "height=\""};
    for (const char* attr : attrs) {
        size_t pos = 0;
        while ((pos = svg_tag.find(attr, pos)) != std::string::npos) {
            size_t val_start = pos + strlen(attr);
            size_t val_end = svg_tag.find('"', val_start);
            if (val_end == std::string::npos) break;
            std::string val_str = svg_tag.substr(val_start, val_end - val_start);
            if (val_str.size() >= 2 && val_str[val_str.size() - 2] == 'e' && val_str[val_str.size() - 1] == 'x') {
                std::string num_part = val_str.substr(0, val_str.size() - 2);
                char* endp = nullptr;
                float ex_val = strtof(num_part.c_str(), &endp);
                if (endp != num_part.c_str()) {
                    float pt_val = ex_val * ex_to_pt;
                    char buf[32];
                    snprintf(buf, sizeof(buf), "%.3fpt", pt_val);
                    svg_tag.replace(val_start, val_end - val_start, buf);
                    val_end = val_start + strlen(buf);
                }
            }
            pos = val_end;
        }
    }

    const char* style_kw = "vertical-align: ";
    size_t style_pos = svg_tag.find(style_kw);
    if (style_pos != std::string::npos) {
        size_t val_start = style_pos + strlen(style_kw);
        size_t val_end = val_start;
        while (val_end < svg_tag.size() && svg_tag[val_end] != ';' && svg_tag[val_end] != '"' && svg_tag[val_end] != ' ') {
            val_end++;
        }
        std::string val_str = svg_tag.substr(val_start, val_end - val_start);
        if (val_str.size() >= 2 && val_str[val_str.size() - 2] == 'e' && val_str[val_str.size() - 1] == 'x') {
            std::string num_part = val_str.substr(0, val_str.size() - 2);
            char* endp = nullptr;
            float ex_val = strtof(num_part.c_str(), &endp);
            if (endp != num_part.c_str()) {
                float pt_val = ex_val * ex_to_pt;
                char buf[32];
                snprintf(buf, sizeof(buf), "%.3fpt", pt_val);
                svg_tag.replace(val_start, val_end - val_start, buf);
            }
        }
    }

    svg.replace(svg_start, svg_end - svg_start + 1, svg_tag);
}

// 清理 MathJax SVG 中 QtSvg 等非浏览器渲染器不兼容的属性:
// 1. 替换硬编码颜色为 caller 指定的颜色,或剥离 (color 为空时)
//    MathJax 默认输出 fill="#000000",color 参数让调用方控制公式颜色。
// 2. 移除 stroke-width (零宽笔画,QtSvg 处理有 bug)
void sanitize_svg_for_renderers(std::string& svg, const char* color) {
    std::string fill_color = color ? color : "";

    if (!fill_color.empty()) {
        // 替换 fill="currentColor" → fill="<color>"
        size_t pos = 0;
        while ((pos = svg.find("fill=\"currentColor\"", pos)) != std::string::npos) {
            std::string replacement = "fill=\"" + fill_color + "\"";
            svg.replace(pos, strlen("fill=\"currentColor\""), replacement);
            pos += replacement.size();
        }
        // 替换 stroke="currentColor" → stroke="<color>"
        pos = 0;
        while ((pos = svg.find("stroke=\"currentColor\"", pos)) != std::string::npos) {
            std::string replacement = "stroke=\"" + fill_color + "\"";
            svg.replace(pos, strlen("stroke=\"currentColor\""), replacement);
            pos += replacement.size();
        }
        // 替换 fill="#000000" → fill="<color>"
        pos = 0;
        while ((pos = svg.find("fill=\"#000000\"", pos)) != std::string::npos) {
            std::string replacement = "fill=\"" + fill_color + "\"";
            svg.replace(pos, strlen("fill=\"#000000\""), replacement);
            pos += replacement.size();
        }
        // 替换 stroke="#000000" → stroke="<color>"
        pos = 0;
        while ((pos = svg.find("stroke=\"#000000\"", pos)) != std::string::npos) {
            std::string replacement = "stroke=\"" + fill_color + "\"";
            svg.replace(pos, strlen("stroke=\"#000000\""), replacement);
            pos += replacement.size();
        }
    } else {
        // 剥离所有显式 fill/stroke,让 SVG 使用默认值 (黑色)
        size_t pos = 0;
        while ((pos = svg.find("fill=\"currentColor\"", pos)) != std::string::npos) {
            svg.erase(pos, strlen("fill=\"currentColor\""));
            if (pos < svg.size() && svg[pos] == ' ') svg.erase(pos, 1);
        }
        pos = 0;
        while ((pos = svg.find("stroke=\"currentColor\"", pos)) != std::string::npos) {
            svg.erase(pos, strlen("stroke=\"currentColor\""));
            if (pos < svg.size() && svg[pos] == ' ') svg.erase(pos, 1);
        }
        pos = 0;
        while ((pos = svg.find("fill=\"#000000\"", pos)) != std::string::npos) {
            svg.erase(pos, strlen("fill=\"#000000\""));
            if (pos < svg.size() && svg[pos] == ' ') svg.erase(pos, 1);
        }
        pos = 0;
        while ((pos = svg.find("stroke=\"#000000\"", pos)) != std::string::npos) {
            svg.erase(pos, strlen("stroke=\"#000000\""));
            if (pos < svg.size() && svg[pos] == ' ') svg.erase(pos, 1);
        }
    }

    // 移除 stroke-width 属性 (零宽笔画,QtSvg 处理有 bug)
    size_t pos = 0;
    while ((pos = svg.find("stroke-width=\"", pos)) != std::string::npos) {
        size_t val_start = pos + strlen("stroke-width=\"");
        size_t val_end = svg.find('"', val_start);
        if (val_end != std::string::npos) {
            svg.erase(pos, val_end - pos + 1);
            if (pos < svg.size() && svg[pos] == ' ') {
                svg.erase(pos, 1);
            }
        } else {
            pos = val_start;
        }
    }
}

}  // namespace

// ============================================================
// C API
// ============================================================

extern "C" {

#define MJX_EXPORT __declspec(dllexport)

// 初始化 MathJax (加载 bundle)
//   bundle_path_in: MathJax bundle JS 文件路径
//   返回 0 表示成功,非 0 表示失败
static int init_state(JsState* st, const char* bundle_path_in) {
    if (st->initialized) return 0;

    if (!bundle_path_in || !*bundle_path_in) {
        bundle_path_in = bundle_path();
    }
    if (!bundle_path_in) {
        return 1;  // no bundle path
    }

    int bundle_len = 0;
    char* bundle = read_file(bundle_path_in, &bundle_len);
    if (!bundle) {
        return 2;  // cannot read bundle
    }

    // ex -> pt 的比例取决于 bundle 里数学字体的 x-height
    read_ex_height_env();

    st->rt = JS_NewRuntime();
    if (!st->rt) {
        free(bundle);
        return 3;
    }
    JS_SetMemoryLimit(st->rt, 256 * 1024 * 1024);
    refresh_stack_budget(st->rt);

    st->ctx = JS_NewContext(st->rt);
    if (!st->ctx) {
        JS_FreeRuntime(st->rt);
        st->rt = nullptr;
        free(bundle);
        return 4;
    }

    // 注入 polyfill
    JSValue poly_val = JS_Eval(st->ctx, POLYFILL, strlen(POLYFILL),
                                "<polyfill>", JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(poly_val)) {
        JS_FreeValue(st->ctx, poly_val);
        JS_FreeContext(st->ctx);
        JS_FreeRuntime(st->rt);
        st->ctx = nullptr;
        st->rt = nullptr;
        free(bundle);
        return 5;
    }
    JS_FreeValue(st->ctx, poly_val);

    // 加载 bundle
    JSValue bundle_val = JS_Eval(st->ctx, bundle, bundle_len,
                                   "<bundle>", JS_EVAL_TYPE_GLOBAL);
    free(bundle);
    if (JS_IsException(bundle_val)) {
        JS_FreeValue(st->ctx, bundle_val);
        JS_FreeContext(st->ctx);
        JS_FreeRuntime(st->rt);
        st->ctx = nullptr;
        st->rt = nullptr;
        return 6;
    }
    JS_FreeValue(st->ctx, bundle_val);

    // 验证 render 函数存在
    JSValue global = JS_GetGlobalObject(st->ctx);
    JSValue render_fn = JS_GetPropertyStr(st->ctx, global, "render");
    bool ok = !JS_IsUndefined(render_fn) && !JS_IsException(render_fn);
    JS_FreeValue(st->ctx, render_fn);
    JS_FreeValue(st->ctx, global);
    if (!ok) {
        JS_FreeContext(st->ctx);
        JS_FreeRuntime(st->rt);
        st->ctx = nullptr;
        st->rt = nullptr;
        return 7;
    }

    st->path = bundle_path_in ? bundle_path_in : "";
    st->initialized = true;
    return 0;
}

// 释放资源
static void shutdown_state(JsState* st) {
    if (!st->initialized) return;
    if (st->ctx) {
        JS_RunGC(st->rt);
        JS_FreeContext(st->ctx);
        st->ctx = nullptr;
    }
    if (st->rt) {
        JS_FreeRuntime(st->rt);
        st->rt = nullptr;
    }
    st->initialized = false;
}

// 渲染 LaTeX -> SVG
//   tex_utf8: LaTeX 源码 (UTF-8)
//   text_size: 字体大小 (pt),用于 ex->pt 转换;0 表示保留 ex 单位
//   display: 1 = display mode, 0 = inline mode
//   color: 公式填充色 (如 "#ff0000");NULL 或空串则剥离所有硬编码颜色
//   out_svg: 输出 SVG 字符串 (调用方用 mathjax_free 释放)
//   out_len: SVG 长度
//   out_width_pt: 输出 SVG 宽度 (pt),0 表示失败
//   out_height_pt: 输出 SVG 高度 (pt)
//   out_baseline_pt: 输出基线偏移 (pt,负值表示向下偏移)
//   out_error: 错误信息 (调用方用 mathjax_free 释放)
//   返回 0 表示成功
static int render_state(
    JsState* st,
    const char* fn_name_in,
    const char* tex_utf8,
    float text_size,
    int display,
    const char* color,
    int postprocess,
    char** out_svg,
    size_t* out_len,
    float* out_width_pt,
    float* out_height_pt,
    float* out_baseline_pt,
    char** out_error
) {
    if (out_svg) *out_svg = nullptr;
    if (out_len) *out_len = 0;
    if (out_width_pt) *out_width_pt = 0;
    if (out_height_pt) *out_height_pt = 0;
    if (out_baseline_pt) *out_baseline_pt = 0;
    if (out_error) *out_error = nullptr;

    if (!st->initialized) {
        if (out_error) *out_error = dup_string("MathJax not initialized");
        return 1;
    }
    if (!tex_utf8) {
        if (out_error) *out_error = dup_string("tex_utf8 is null");
        return 2;
    }

    // may be called from a different thread than the one that initialized
    // the runtime (GUI worker vs main thread) -- re-anchor the JS stack
    refresh_stack_budget(st->rt);

    JSValue global = JS_GetGlobalObject(st->ctx);
    const char* fn_name = (fn_name_in && *fn_name_in)
        ? fn_name_in : (display ? "render" : "renderInline");
    JSValue render_fn = JS_GetPropertyStr(st->ctx, global, fn_name);
    JS_FreeValue(st->ctx, global);

    if (JS_IsUndefined(render_fn) || JS_IsException(render_fn)) {
        JS_FreeValue(st->ctx, render_fn);
        char buf[128];
        snprintf(buf, sizeof(buf), "%s() not defined in bundle", fn_name);
        if (out_error) *out_error = dup_string(buf);
        return 3;
    }

    JSValue latex_val = JS_NewString(st->ctx, tex_utf8);
    JSValue result = JS_Call(st->ctx, render_fn, JS_UNDEFINED, 1, &latex_val);
    JS_FreeValue(st->ctx, latex_val);
    JS_FreeValue(st->ctx, render_fn);

    if (JS_IsException(result)) {
        JSValue err = JS_GetException(st->ctx);
        JSValue msg = JS_GetPropertyStr(st->ctx, err, "message");
        const char* err_str = nullptr;
        if (JS_IsString(msg)) {
            err_str = JS_ToCString(st->ctx, msg);
        } else {
            err_str = JS_ToCString(st->ctx, err);
        }
        if (err_str) {
            if (out_error) *out_error = dup_string(err_str);
            JS_FreeCString(st->ctx, err_str);
        } else {
            if (out_error) *out_error = dup_string("unknown JS exception");
        }
        JS_FreeValue(st->ctx, msg);
        JS_FreeValue(st->ctx, err);
        JS_FreeValue(st->ctx, result);
        return 4;
    }

    if (!JS_IsString(result)) {
        if (out_error) *out_error = dup_string("render() did not return a string");
        JS_FreeValue(st->ctx, result);
        return 5;
    }

    const char* svg_str = JS_ToCString(st->ctx, result);
    if (!svg_str) {
        if (out_error) *out_error = dup_string("JS_ToCString failed (OOM?)");
        JS_FreeValue(st->ctx, result);
        return 6;
    }

    // 拷贝 SVG 到我们自己的内存
    size_t svg_len = strlen(svg_str);
    char* svg_copy = (char*)malloc(svg_len + 1);
    if (!svg_copy) {
        JS_FreeCString(st->ctx, svg_str);
        JS_FreeValue(st->ctx, result);
        if (out_error) *out_error = dup_string("OOM copying SVG");
        return 7;
    }
    memcpy(svg_copy, svg_str, svg_len + 1);
    JS_FreeCString(st->ctx, svg_str);
    JS_FreeValue(st->ctx, result);

    // postprocess=0: 原样返回(Non-SVG bundle, 如 KaTeX 的 MathML)
    if (!postprocess) {
        if (out_svg) *out_svg = svg_copy;
        if (out_len) *out_len = svg_len;
        return 0;
    }

    // 解析 SVG 的 width/height/vertical-align (ex 单位)
    char width_ex[32] = {0};
    char height_ex[32] = {0};
    char valign_ex[32] = {0};
    parse_svg_metrics(svg_copy, width_ex, sizeof(width_ex),
                      height_ex, sizeof(height_ex),
                      valign_ex, sizeof(valign_ex));

    // 转换成 pt (1ex = text_size * x-height pt)
    if (text_size > 0) {
        std::string svg_std(svg_copy);
        rewrite_svg_units(svg_std, text_size);
        sanitize_svg_for_renderers(svg_std, color);
        free(svg_copy);
        svg_copy = dup_string(svg_std.c_str());
        svg_len = strlen(svg_copy);
    } else {
        // 即使不转单位,也要清理 QtSvg 不兼容的属性
        std::string svg_std(svg_copy);
        sanitize_svg_for_renderers(svg_std, color);
        if (svg_std.size() != svg_len) {
            free(svg_copy);
            svg_copy = dup_string(svg_std.c_str());
            svg_len = strlen(svg_copy);
        }
    }

    // 输出 pt 值: 与 rewrite_svg_units() 用同一个换算式
    //     1ex = text_size_pt * g_ex_height   (pt)
    // ex_to_size() 的结果单位跟着 size 参数的单位走,所以这里传 pt 进去。
    // 注意: 这里以前把 size 先按 96dpi 转成了像素再乘,而调用方(插件)按 pt
    // 使用返回值、再乘 dpi/72 换算成像素,于是公式整体被放大 4/3 (约 33%),
    // 这就是"MathJax 出来的字比正常字体大"的原因。
    float size_pt = (text_size > 0) ? text_size : 20.0f;
    if (out_width_pt) *out_width_pt = ex_to_size(width_ex, size_pt);
    if (out_height_pt) *out_height_pt = ex_to_size(height_ex, size_pt);
    if (out_baseline_pt) *out_baseline_pt = ex_to_size(valign_ex, size_pt);

    if (out_svg) *out_svg = svg_copy;
    if (out_len) *out_len = svg_len;
    return 0;
}

// ============================================================
// 旧 API (MathJax 沿用,操作第一个初始化的 bundle)
// ============================================================

MJX_EXPORT int mathjax_initialize(const char* bundle_path_in) {
    return init_state(primary_state(), bundle_path_in);
}

MJX_EXPORT void mathjax_shutdown() {
    if (g_primary) shutdown_state(g_primary);
}

MJX_EXPORT int mathjax_render_svg(
    const char* tex_utf8,
    float text_size,
    int display,
    const char* color,
    char** out_svg,
    size_t* out_len,
    float* out_width_pt,
    float* out_height_pt,
    float* out_baseline_pt,
    char** out_error
) {
    return render_state(primary_state(), nullptr, tex_utf8, text_size, display,
                        color, 1, out_svg, out_len, out_width_pt,
                        out_height_pt, out_baseline_pt, out_error);
}

// ============================================================
// 通用 JS 宿主 API
//
// js_host_init() 把任意 bundle 作为独立运行时加载并返回句柄 (>0),同一路径
// 只加载一次;js_host_render() 在该运行时里调用 fn_name 并把结果字符串**原样**
// 返回(postprocess=0,不做 MathJax 专用的 ex->pt / 颜色处理),因此 MathML 之类
// 的非 SVG 输出也能直接用。
// ============================================================

MJX_EXPORT int js_host_init(const char* bundle_path_in) {
    if (!bundle_path_in || !*bundle_path_in) return 0;
    for (size_t i = 0; i < g_states.size(); ++i) {
        if (g_states[i]->initialized && g_states[i]->path == bundle_path_in) {
            return static_cast<int>(i) + 1;
        }
    }
    JsState* st = new JsState();
    g_states.push_back(st);
    if (init_state(st, bundle_path_in) != 0) {
        g_states.pop_back();
        delete st;
        return 0;
    }
    // g_primary is deliberately NOT claimed here: it belongs to the mathjax_*
    // API, which creates its own state on first use.  Otherwise loading some
    // other bundle first would turn mathjax_initialize() into a no-op on that
    // runtime, and MathJax would be rendered by the wrong JS bundle.
    return static_cast<int>(g_states.size());
}

MJX_EXPORT void js_host_shutdown(int handle) {
    JsState* st = state_for_handle(handle);
    if (st) shutdown_state(st);
}

// js_host_eval() 在已经加载的运行时里再执行一个脚本文件。
//
// 这是字体数据文件挂进已有内核的方式:一个字体文件只注册字体数据(它从
// __veuszMathjax 拿内核暴露的 FontData/SvgFontData 并调用 registerFont),
// 本身不含 MathJax,所以同一个内核可以被十几种字体共用,而不是每种字体带一份。
// 返回 0 成功;1 无效句柄,2 读不到文件,3 脚本抛异常(错误写在 out_error)。
MJX_EXPORT int js_host_eval(int handle, const char* script_path, char** out_error) {
    JsState* st = state_for_handle(handle);
    if (!st || !st->ctx) {
        if (out_error) *out_error = dup_string("invalid js host handle");
        return 1;
    }
    if (!script_path || !*script_path) {
        if (out_error) *out_error = dup_string("no script path");
        return 2;
    }
    int len = 0;
    char* src = read_file(script_path, &len);
    if (!src) {
        if (out_error) *out_error = dup_string("cannot read the script");
        return 2;
    }
    JSValue val = JS_Eval(st->ctx, src, static_cast<size_t>(len), script_path,
                          JS_EVAL_TYPE_GLOBAL);
    free(src);
    if (JS_IsException(val)) {
        JSValue err = JS_GetException(st->ctx);
        if (out_error) {
            JSValue msg = JS_GetPropertyStr(st->ctx, err, "message");
            const char* err_str = nullptr;
            if (!JS_IsUndefined(msg) && !JS_IsException(msg)) {
                err_str = JS_ToCString(st->ctx, msg);
            }
            if (!err_str) err_str = JS_ToCString(st->ctx, err);
            *out_error = dup_string(err_str ? err_str : "unknown JS exception");
            if (err_str) JS_FreeCString(st->ctx, err_str);
            JS_FreeValue(st->ctx, msg);
        }
        JS_FreeValue(st->ctx, err);
        JS_FreeValue(st->ctx, val);
        return 3;
    }
    JS_FreeValue(st->ctx, val);
    return 0;
}

MJX_EXPORT int js_host_render(
    int handle,
    const char* fn_name,
    const char* input_utf8,
    float text_size,
    int display,
    const char* color,
    int postprocess,
    char** out_text,
    size_t* out_len,
    float* out_width_pt,
    float* out_height_pt,
    float* out_baseline_pt,
    char** out_error
) {
    JsState* st = state_for_handle(handle);
    if (!st) {
        if (out_error) *out_error = dup_string("invalid js host handle");
        return 1;
    }
    return render_state(st, fn_name, input_utf8, text_size, display, color,
                        postprocess, out_text, out_len, out_width_pt,
                        out_height_pt, out_baseline_pt, out_error);
}

MJX_EXPORT void mathjax_free(void* p) {
    free(p);
}

// 换字体时必须重设 ex-height: 每个 MathJax 字体声明的 x-height 不同
// (newcm 0.442 / pagella 0.482 / stix2 0.479 / dejavu 0.519 ...),而
// 1ex = text_size * x-height 决定了 SVG 尺寸换算,写死一个值会让别的字体
// 整体偏大或偏小(dejavu 会小 17%)。
// 插件在切换字体时调用,值来自 fonts.json (构建时从字体包读出)。
MJX_EXPORT void mathjax_set_ex_height(float value) {
    if (value > 0.05f && value < 2.0f) {
        g_ex_height = value;
    }
}

// 供测试/诊断: 读出当前使用的 ex-height
MJX_EXPORT float mathjax_get_ex_height(void) {
    return g_ex_height;
}

}  // extern "C"
