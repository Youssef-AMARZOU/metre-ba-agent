// fast_wall_core.cpp — Noyau natif des boucles critiques du pipeline murs.
//
// Module C++ monolithique, sans dépendance externe, avec interface C
// (extern "C") manipulant directement les pointeurs mémoire des tableaux
// NumPy (zéro copie via ctypes) :
//   1. estimate_stroke_width_fused_c  : épaisseur de trait (squelette +
//                                       distance) en O(K) par pointeurs ;
//   2. estimate_stroke_width_roi_c    : épaisseur autonome (EDT 2D exacte de
//                                       Meijster/Felzenszwalb + Zhang-Suen) ;
//   3. reconstruct_dotted_line_c      : murs pointillés (RANSAC orthogonal
//                                       analytique + coupure aux ouvertures) ;
//   4. deduplicate_and_merge_segments_c : déduplication + raccordement
//                                       colinéaire avant appariement ;
//   5. pair_all_wall_faces_c          : appariement O(M^2) des faces parallèles
//                                       en murs à double trait.
//
// Compilation (voir scripts/build_fast_wall.py, ou compilation automatique
// dans extractors/fast_wall_bridge.py) :
//   Linux / WSL (g++) : g++ -O3 -march=native -ffast-math -std=c++17 -shared -fPIC fast_wall_core.cpp -o libfastwall.so
//   Windows (MSVC)    : cl /O2 /fp:fast /std:c++17 /LD fast_wall_core.cpp /Fe:fastwall.dll
//
// Ce noyau est OPTIONNEL : fast_wall_bridge.py bascule sur l'implémentation
// Python pure si la bibliothèque est absente.
#include <cmath>
#include <cstdint>
#include <algorithm>
#include <vector>

#if defined(_WIN32) || defined(_WIN64)
#define FASTWALL_EXPORT __declspec(dllexport)
#else
#define FASTWALL_EXPORT __attribute__((visibility("default")))
#endif

extern "C" {

struct StrokeWidthResultC {
    double width_median_px;
    double width_q25_px;
    double width_q75_px;
    int32_t samples;
    int32_t confidence_code; // 0 = low (samples < 30), 1 = candidate
};

struct DottedSegmentC {
    double x1, y1, x2, y2;
    double length_px;
    double dot_pitch_px;
    double estimated_gap_px;
    double ransac_sigma_px;
    int32_t local_max_gap_px;
    int32_t support_count;
    int32_t confidence_level; // 2 = high, 1 = candidate
};

struct WallPairC {
    double cx1, cy1, cx2, cy2; // Axe central (centerline)
    double ax1, ay1, ax2, ay2; // Face A tronquee sur le recouvrement
    double bx1, by1, bx2, by2; // Face B tronquee sur le recouvrement
    double width_px;
    double overlap_px;
    double overlap_ratio;
    double angle_diff_deg;
    int32_t idx_a;
    int32_t idx_b;
};

} // extern "C"

namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr double kRadToDeg = 180.0 / kPi;
constexpr double kDegToRad = kPi / 180.0;

static inline uint64_t xorshift64star(uint64_t* state) {
    uint64_t x = *state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *state = x;
    return x * 0x2545F4914F6CDD1DULL;
}

static inline double fast_median_ptr(double* __restrict buf, int32_t n) {
    if (n <= 0) return 0.0;
    int32_t mid = n >> 1;
    std::nth_element(buf, buf + mid, buf + n);
    double m1 = buf[mid];
    if ((n & 1) != 0) return m1;
    double m0 = *std::max_element(buf, buf + mid);
    return 0.5 * (m0 + m1);
}

static inline double fast_quantile_ptr(double* __restrict buf, int32_t n, double q) {
    if (n <= 0) return 0.0;
    if (n == 1) return buf[0];
    std::sort(buf, buf + n);
    double pos = q * (n - 1);
    int32_t lo = static_cast<int32_t>(pos);
    int32_t hi = std::min(n - 1, lo + 1);
    double frac = pos - lo;
    return buf[lo] * (1.0 - frac) + buf[hi] * frac;
}

static inline void compute_quartiles_linear_ptr(
    float* __restrict buf,
    int32_t n,
    double* __restrict out_q25,
    double* __restrict out_q50,
    double* __restrict out_q75
) {
    if (n <= 0) {
        *out_q25 = 0.0; *out_q50 = 0.0; *out_q75 = 0.0;
        return;
    }
    if (n == 1) {
        double v = static_cast<double>(buf[0]);
        *out_q25 = v; *out_q50 = v; *out_q75 = v;
        return;
    }

    int32_t i50 = n >> 1;
    int32_t i25 = n >> 2;
    int32_t i75 = (n * 3) >> 2;

    std::nth_element(buf, buf + i50, buf + n);
    double med = static_cast<double>(buf[i50]);
    if ((n & 1) == 0) {
        float max_low = *std::max_element(buf, buf + i50);
        med = 0.5 * (static_cast<double>(max_low) + med);
    }
    *out_q50 = med;

    std::nth_element(buf, buf + i25, buf + i50);
    *out_q25 = static_cast<double>(buf[i25]);

    if (i75 > i50) {
        std::nth_element(buf + i50 + 1, buf + i75, buf + n);
    }
    *out_q75 = static_cast<double>(buf[i75]);
}

static inline void fit_line_2d_analytic(
    const double* __restrict pts_x,
    const double* __restrict pts_y,
    const uint8_t* __restrict mask,
    int32_t n,
    double* __restrict out_cx,
    double* __restrict out_cy,
    double* __restrict out_ux,
    double* __restrict out_uy
) {
    double sum_x = 0.0, sum_y = 0.0;
    int32_t count = 0;
    for (int32_t i = 0; i < n; ++i) {
        if (mask[i]) {
            sum_x += pts_x[i];
            sum_y += pts_y[i];
            ++count;
        }
    }
    if (count < 2) {
        *out_cx = 0.0; *out_cy = 0.0;
        *out_ux = 1.0; *out_uy = 0.0;
        return;
    }
    double inv_n = 1.0 / static_cast<double>(count);
    double cx = sum_x * inv_n;
    double cy = sum_y * inv_n;

    double sxx = 0.0, syy = 0.0, sxy = 0.0;
    for (int32_t i = 0; i < n; ++i) {
        if (mask[i]) {
            double dx = pts_x[i] - cx;
            double dy = pts_y[i] - cy;
            sxx += dx * dx;
            syy += dy * dy;
            sxy += dx * dy;
        }
    }

    double theta = 0.5 * std::atan2(2.0 * sxy, sxx - syy);
    *out_cx = cx;
    *out_cy = cy;
    *out_ux = std::cos(theta);
    *out_uy = std::sin(theta);
}

static void exact_edt_2d_ptr(
    const uint8_t* __restrict bin,
    int32_t w,
    int32_t h,
    int32_t src_stride,
    float* __restrict dist_out
) {
    const int32_t INF = w + h + 10;
    std::vector<int32_t> g(w * h, 0);

    for (int32_t x = 0; x < w; ++x) {
        int32_t d = (bin[x] == 0) ? 0 : INF;
        g[x] = d * d;
        for (int32_t y = 1; y < h; ++y) {
            d = (bin[y * src_stride + x] == 0) ? 0 : (d + 1);
            g[y * w + x] = d * d;
        }
        for (int32_t y = h - 2; y >= 0; --y) {
            int32_t d_down = g[(y + 1) * w + x];
            int32_t step_down = (d_down < INF * INF) ? (static_cast<int32_t>(std::sqrt(d_down)) + 1) : INF;
            int32_t sq = step_down * step_down;
            if (sq < g[y * w + x]) {
                g[y * w + x] = sq;
            }
        }
    }

    std::vector<int32_t> v(w);
    std::vector<int32_t> z(w + 1);

    for (int32_t y = 0; y < h; ++y) {
        const int32_t* __restrict g_row = g.data() + y * w;
        float* __restrict d_row = dist_out + y * w;

        int32_t k = 0;
        v[0] = 0;
        z[0] = -INF * 2;
        z[1] = +INF * 2;

        for (int32_t q = 1; q < w; ++q) {
            int32_t s;
            while (true) {
                int32_t vk = v[k];
                int32_t num = (g_row[q] + q * q) - (g_row[vk] + vk * vk);
                int32_t den = 2 * (q - vk);
                s = num / den;
                if (num < 0 && (num % den != 0)) --s;
                if (s > z[k]) break;
                --k;
            }
            ++k;
            v[k] = q;
            z[k] = s;
            z[k + 1] = +INF * 2;
        }

        k = 0;
        for (int32_t q = 0; q < w; ++q) {
            while (z[k + 1] < q) {
                ++k;
            }
            int32_t dx = q - v[k];
            int32_t dist_sq = dx * dx + g_row[v[k]];
            d_row[q] = (g_row[q] == 0) ? 0.0f : std::sqrt(static_cast<float>(dist_sq));
        }
    }
}

static void zhang_suen_thinning_ptr(uint8_t* __restrict im, int32_t w, int32_t h) {
    std::vector<int32_t> candidates;
    candidates.reserve((w * h) >> 3);

    for (int32_t y = 1; y < h - 1; ++y) {
        int32_t row = y * w;
        for (int32_t x = 1; x < w - 1; ++x) {
            if (im[row + x]) {
                candidates.push_back(row + x);
            }
        }
    }

    std::vector<int32_t> to_clear;
    to_clear.reserve(candidates.size() >> 1);
    std::vector<int32_t> next_candidates;
    next_candidates.reserve(candidates.size());

    bool changed = true;
    while (changed && !candidates.empty()) {
        changed = false;

        for (int pass = 0; pass < 2; ++pass) {
            to_clear.clear();
            for (int32_t idx : candidates) {
                if (!im[idx]) continue;

                uint8_t p2 = im[idx - w];
                uint8_t p3 = im[idx - w + 1];
                uint8_t p4 = im[idx + 1];
                uint8_t p5 = im[idx + w + 1];
                uint8_t p6 = im[idx + w];
                uint8_t p7 = im[idx + w - 1];
                uint8_t p8 = im[idx - 1];
                uint8_t p9 = im[idx - w - 1];

                int32_t B = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9;
                if (B < 2 || B > 6) continue;

                int32_t A = (!p2 && p3) + (!p3 && p4) + (!p4 && p5) + (!p5 && p6) +
                            (!p6 && p7) + (!p7 && p8) + (!p8 && p9) + (!p9 && p2);
                if (A != 1) continue;

                if (pass == 0) {
                    if ((p2 & p4 & p6) == 0 && (p4 & p6 & p8) == 0) {
                        to_clear.push_back(idx);
                    }
                } else {
                    if ((p2 & p4 & p8) == 0 && (p2 & p6 & p8) == 0) {
                        to_clear.push_back(idx);
                    }
                }
            }

            if (!to_clear.empty()) {
                changed = true;
                for (int32_t idx : to_clear) {
                    im[idx] = 0;
                }
            }
        }

        if (changed) {
            next_candidates.clear();
            for (int32_t idx : candidates) {
                if (im[idx]) next_candidates.push_back(idx);
            }
            candidates.swap(next_candidates);
        }
    }
}

struct ProjectedDot {
    double s;
    double d;
};

struct WorkSegment {
    double x0, y0, x1, y1;
    double ux, uy, len;
    bool active;
};

static inline void refresh_segment(WorkSegment& seg) {
    double dx = seg.x1 - seg.x0;
    double dy = seg.y1 - seg.y0;
    double l = std::hypot(dx, dy);
    seg.len = l;
    if (l < 1e-6) {
        seg.active = false;
        seg.ux = 1.0;
        seg.uy = 0.0;
    } else {
        seg.ux = dx / l;
        seg.uy = dy / l;
    }
}

} // namespace

extern "C" {

/**
 * 1A. Estimation fusionnee de l'epaisseur des traits sur pointeurs skeleton + distance.
 */
FASTWALL_EXPORT void estimate_stroke_width_fused_c(
    const uint8_t* __restrict skeleton,
    const float* __restrict distance,
    int32_t width,
    int32_t height,
    int32_t skel_stride,
    int32_t dist_stride,
    StrokeWidthResultC* __restrict out_res
) {
    std::vector<float> widths;
    widths.reserve((width * height) >> 4);

    for (int32_t y = 1; y < height - 1; ++y) {
        const uint8_t* __restrict s_row = skeleton + y * skel_stride;
        const float* __restrict d_row = distance + y * dist_stride;

        for (int32_t x = 1; x < width - 1; ++x) {
            if (s_row[x] == 0) continue;

            const uint8_t* p = s_row + x;
            int32_t neighbors =
                (p[-skel_stride - 1] != 0) + (p[-skel_stride] != 0) + (p[-skel_stride + 1] != 0) +
                (p[-1]               != 0)                          + (p[+1]               != 0) +
                (p[+skel_stride - 1] != 0) + (p[+skel_stride] != 0) + (p[+skel_stride + 1] != 0);

            if (neighbors == 2) {
                float r = d_row[x];
                float w_px = std::max(1.0f, 2.0f * r - 1.0f);
                widths.push_back(w_px);
            }
        }
    }

    int32_t n_samples = static_cast<int32_t>(widths.size());
    out_res->samples = n_samples;

    if (n_samples < 30) {
        out_res->width_median_px = -1.0;
        out_res->width_q25_px = -1.0;
        out_res->width_q75_px = -1.0;
        out_res->confidence_code = 0;
        return;
    }

    compute_quartiles_linear_ptr(
        widths.data(),
        n_samples,
        &out_res->width_q25_px,
        &out_res->width_median_px,
        &out_res->width_q75_px
    );
    out_res->confidence_code = 1;
}

/**
 * 1B. Estimation autonome de l'epaisseur sur une ROI binaire (EDT 2D + Zhang-Suen + 3x3).
 */
FASTWALL_EXPORT void estimate_stroke_width_roi_c(
    const uint8_t* __restrict binary_img,
    int32_t img_width,
    int32_t img_height,
    int32_t img_stride,
    int32_t roi_x0,
    int32_t roi_y0,
    int32_t roi_w,
    int32_t roi_h,
    StrokeWidthResultC* __restrict out_res
) {
    int32_t x0 = std::clamp(roi_x0, 0, img_width - 1);
    int32_t y0 = std::clamp(roi_y0, 0, img_height - 1);
    int32_t w = std::min(roi_w, img_width - x0);
    int32_t h = std::min(roi_h, img_height - y0);

    if (w < 5 || h < 5) {
        out_res->width_median_px = -1.0;
        out_res->width_q25_px = -1.0;
        out_res->width_q75_px = -1.0;
        out_res->samples = 0;
        out_res->confidence_code = 0;
        return;
    }

    const uint8_t* roi_ptr = binary_img + y0 * img_stride + x0;

    std::vector<float> dist_buf(w * h);
    exact_edt_2d_ptr(roi_ptr, w, h, img_stride, dist_buf.data());

    std::vector<uint8_t> skel_buf(w * h, 0);
    for (int32_t y = 1; y < h - 1; ++y) {
        const uint8_t* src_row = roi_ptr + y * img_stride;
        uint8_t* dst_row = skel_buf.data() + y * w;
        for (int32_t x = 1; x < w - 1; ++x) {
            dst_row[x] = (src_row[x] != 0) ? 1 : 0;
        }
    }

    zhang_suen_thinning_ptr(skel_buf.data(), w, h);

    estimate_stroke_width_fused_c(
        skel_buf.data(),
        dist_buf.data(),
        w,
        h,
        w,
        w,
        out_res
    );
}

/**
 * 2. Reconstruction locale des murs en pointilles avec RANSAC orthogonal et coupure aux ouvertures.
 */
FASTWALL_EXPORT int32_t reconstruct_dotted_line_c(
    const double* __restrict comp_centers,
    const double* __restrict comp_spans,
    int32_t n_components,
    const double* __restrict hyp_segment,
    double stroke_width_px,
    double k_slenderness,
    int32_t min_support_dots,
    double angle_gate_deg,
    int32_t max_ransac_trials,
    DottedSegmentC* __restrict out_segments,
    int32_t max_out_segments
) {
    double t = std::max(1.0, stroke_width_px);
    double x1 = hyp_segment[0], y1 = hyp_segment[1];
    double x2 = hyp_segment[2], y2 = hyp_segment[3];

    double dx_hyp = x2 - x1;
    double dy_hyp = y2 - y1;
    double seg_len = std::hypot(dx_hyp, dy_hyp);
    if (seg_len < 2.0 || n_components < min_support_dots) {
        return 0;
    }

    double ux_hyp = dx_hyp / seg_len;
    double uy_hyp = dy_hyp / seg_len;
    double nx_hyp = -uy_hyp;
    double ny_hyp = ux_hyp;

    double band_half_width = std::max(2.5, 2.5 * t);
    double s_min_hyp = (x1 * ux_hyp + y1 * uy_hyp) - 4.0 * t;
    double s_max_hyp = (x2 * ux_hyp + y2 * uy_hyp) + 4.0 * t;

    std::vector<double> bx;
    std::vector<double> by;
    std::vector<double> bd;
    bx.reserve(64);
    by.reserve(64);
    bd.reserve(64);

    const double* __restrict c_ptr = comp_centers;
    for (int32_t i = 0; i < n_components; ++i) {
        double cx = c_ptr[0];
        double cy = c_ptr[1];
        c_ptr += 2;

        double perp = std::abs((cx - x1) * nx_hyp + (cy - y1) * ny_hyp);
        if (perp <= band_half_width) {
            double proj_s = cx * ux_hyp + cy * uy_hyp;
            if (proj_s >= s_min_hyp && proj_s <= s_max_hyp) {
                bx.push_back(cx);
                by.push_back(cy);
                bd.push_back(std::max(1.0, comp_spans[i]));
            }
        }
    }

    int32_t nb = static_cast<int32_t>(bx.size());
    if (nb < min_support_dots) {
        return 0;
    }

    double initial_tol = std::max(1.5, 1.25 * t);
    uint64_t rng_state = 0x9E3779B97F4A7C15ULL ^ static_cast<uint64_t>(nb);

    std::vector<uint8_t> best_mask(nb, 0);
    std::vector<uint8_t> curr_mask(nb, 0);
    int32_t best_count = 0;
    double best_res_sum = 1e30;

    const double* __restrict px = bx.data();
    const double* __restrict py = by.data();

    for (int32_t trial = 0; trial < max_ransac_trials; ++trial) {
        int32_t i1 = static_cast<int32_t>(xorshift64star(&rng_state) % static_cast<uint64_t>(nb));
        int32_t i2 = static_cast<int32_t>(xorshift64star(&rng_state) % static_cast<uint64_t>(nb - 1));
        if (i2 >= i1) ++i2;

        double vx = px[i2] - px[i1];
        double vy = py[i2] - py[i1];
        double vnorm = std::hypot(vx, vy);
        if (vnorm < 1e-6) continue;

        double nx = -vy / vnorm;
        double ny = vx / vnorm;
        double p1x = px[i1], p1y = py[i1];

        int32_t count = 0;
        double res_sum = 0.0;
        for (int32_t k = 0; k < nb; ++k) {
            double r = std::abs((px[k] - p1x) * nx + (py[k] - p1y) * ny);
            if (r <= initial_tol) {
                curr_mask[k] = 1;
                ++count;
                res_sum += r;
            } else {
                curr_mask[k] = 0;
            }
        }

        if (count > best_count || (count == best_count && count > 0 && res_sum < best_res_sum)) {
            best_count = count;
            best_res_sum = res_sum;
            std::copy(curr_mask.begin(), curr_mask.end(), best_mask.begin());
        }
    }

    if (best_count < min_support_dots) {
        return 0;
    }

    double cx_line, cy_line, ux_line, uy_line;
    fit_line_2d_analytic(px, py, best_mask.data(), nb, &cx_line, &cy_line, &ux_line, &uy_line);
    double nx_line = -uy_line, ny_line = ux_line;

    std::vector<double> scratch(best_count);
    int32_t idx = 0;
    for (int32_t k = 0; k < nb; ++k) {
        if (best_mask[k]) {
            scratch[idx++] = (px[k] - cx_line) * nx_line + (py[k] - cy_line) * ny_line;
        }
    }

    double med_r = fast_median_ptr(scratch.data(), best_count);
    for (int32_t k = 0; k < best_count; ++k) {
        scratch[k] = std::abs(scratch[k] - med_r);
    }
    double mad = fast_median_ptr(scratch.data(), best_count);
    double sigma = 1.4826 * mad;
    double tau = std::clamp(std::max(1.0, 2.5 * sigma), 1.0, 2.0 * t);

    int32_t refined_count = 0;
    for (int32_t k = 0; k < nb; ++k) {
        double r = std::abs((px[k] - cx_line) * nx_line + (py[k] - cy_line) * ny_line);
        if (r <= tau) {
            curr_mask[k] = 1;
            ++refined_count;
        } else {
            curr_mask[k] = 0;
        }
    }
    const uint8_t* final_mask = (refined_count >= min_support_dots) ? curr_mask.data() : best_mask.data();
    int32_t final_count = (refined_count >= min_support_dots) ? refined_count : best_count;

    fit_line_2d_analytic(px, py, final_mask, nb, &cx_line, &cy_line, &ux_line, &uy_line);
    if (ux_line * ux_hyp + uy_line * uy_hyp < 0.0) {
        ux_line = -ux_line;
        uy_line = -uy_line;
    }

    double cos_ang = std::clamp(ux_line * ux_hyp + uy_line * uy_hyp, -1.0, 1.0);
    double angle_deg = std::acos(cos_ang) * kRadToDeg;
    if (angle_deg > angle_gate_deg) {
        return 0;
    }

    std::vector<ProjectedDot> proj(final_count);
    int32_t p_idx = 0;
    for (int32_t k = 0; k < nb; ++k) {
        if (final_mask[k]) {
            proj[p_idx].s = (px[k] - cx_line) * ux_line + (py[k] - cy_line) * uy_line;
            proj[p_idx].d = bd[k];
            ++p_idx;
        }
    }

    std::sort(proj.begin(), proj.end(), [](const ProjectedDot& a, const ProjectedDot& b) {
        return a.s < b.s;
    });

    int32_t n_intervals = final_count - 1;
    std::vector<double> pitches(n_intervals);
    std::vector<double> gaps(n_intervals);
    std::vector<double> plausible_gaps;
    std::vector<double> plausible_pitches;
    plausible_gaps.reserve(n_intervals);
    plausible_pitches.reserve(n_intervals);

    for (int32_t i = 0; i < n_intervals; ++i) {
        double p_val = proj[i + 1].s - proj[i].s;
        double g_val = std::max(0.0, p_val - 0.5 * (proj[i].d + proj[i + 1].d));
        pitches[i] = p_val;
        gaps[i] = g_val;
        if (g_val > 0.25 * t && g_val <= 25.0 * t) {
            plausible_gaps.push_back(g_val);
            plausible_pitches.push_back(p_val);
        }
    }

    if (plausible_gaps.empty()) {
        return 0;
    }

    double median_gap = fast_median_ptr(plausible_gaps.data(), static_cast<int32_t>(plausible_gaps.size()));
    double median_pitch = fast_median_ptr(plausible_pitches.data(), static_cast<int32_t>(plausible_pitches.size()));
    double break_threshold = std::max(3.0 * median_gap + 2.0 * t, 2.2 * median_pitch);

    int32_t out_count = 0;
    int32_t grp_start = 0;

    for (int32_t i = 0; i <= n_intervals; ++i) {
        bool is_split = (i == n_intervals) || (gaps[i] > break_threshold);
        if (!is_split) continue;

        int32_t grp_len = i - grp_start + 1;
        if (grp_len >= min_support_dots && out_count < max_out_segments) {
            int32_t sub_intervals = grp_len - 1;
            std::vector<double> sub_p(pitches.begin() + grp_start, pitches.begin() + grp_start + sub_intervals);
            std::vector<double> sub_g(gaps.begin() + grp_start, gaps.begin() + grp_start + sub_intervals);

            double p_med = fast_median_ptr(sub_p.data(), sub_intervals);
            double p_q25 = fast_quantile_ptr(sub_p.data(), sub_intervals, 0.25);
            double p_q75 = fast_quantile_ptr(sub_p.data(), sub_intervals, 0.75);
            double rel_disp = (p_q75 - p_q25) / std::max(1.0, p_med);

            if (rel_disp <= 0.65) {
                double g_q90 = fast_quantile_ptr(sub_g.data(), sub_intervals, 0.90);
                double g_med = fast_median_ptr(sub_g.data(), sub_intervals);
                int32_t local_max_gap = static_cast<int32_t>(std::ceil(g_q90 + t));

                double s0 = proj[grp_start].s - 0.5 * proj[grp_start].d;
                double s1 = proj[i].s + 0.5 * proj[i].d;
                double support_len = s1 - s0;
                double l_min_req = std::max(k_slenderness * t, (min_support_dots - 1) * p_med);

                if (support_len >= l_min_req) {
                    DottedSegmentC& dst = out_segments[out_count++];
                    dst.x1 = cx_line + s0 * ux_line;
                    dst.y1 = cy_line + s0 * uy_line;
                    dst.x2 = cx_line + s1 * ux_line;
                    dst.y2 = cy_line + s1 * uy_line;
                    dst.length_px = support_len;
                    dst.dot_pitch_px = p_med;
                    dst.estimated_gap_px = g_med;
                    dst.ransac_sigma_px = sigma;
                    dst.local_max_gap_px = local_max_gap;
                    dst.support_count = grp_len;
                    dst.confidence_level = (rel_disp <= 0.35 && grp_len >= 6) ? 2 : 1;
                }
            }
        }
        grp_start = i + 1;
    }

    return out_count;
}

/**
 * 3. Deduplication des detections multiples d'un meme trait et raccordement colineaire
 *    sans fusionner deux faces paralleles d'un mur (perp_tol_px ~ 1.5 * t).
 */
FASTWALL_EXPORT int32_t deduplicate_and_merge_segments_c(
    const double* __restrict in_segments,
    int32_t n_segments,
    double perp_tol_px,
    double max_collinear_gap_px,
    double angle_tol_deg,
    double* __restrict out_segments,
    int32_t max_out_segments
) {
    if (n_segments <= 0 || max_out_segments <= 0) return 0;

    std::vector<WorkSegment> segs;
    segs.reserve(n_segments);

    for (int32_t i = 0; i < n_segments; ++i) {
        const double* s = in_segments + (i * 4);
        WorkSegment ws{s[0], s[1], s[2], s[3], 0.0, 0.0, 0.0, true};
        refresh_segment(ws);
        if (ws.active) {
            segs.push_back(ws);
        }
    }

    // Tri par longueur decroissante : les segments longs servent d'ancres directrices
    std::sort(segs.begin(), segs.end(), [](const WorkSegment& a, const WorkSegment& b) {
        return a.len > b.len;
    });

    double cos_tol = std::cos(angle_tol_deg * kDegToRad);
    int32_t m = static_cast<int32_t>(segs.size());

    bool merged_any = true;
    while (merged_any) {
        merged_any = false;
        for (int32_t i = 0; i < m; ++i) {
            if (!segs[i].active) continue;

            for (int32_t j = i + 1; j < m; ++j) {
                if (!segs[j].active) continue;

                WorkSegment& a = segs[i];
                WorkSegment& b = segs[j];

                double dot = a.ux * b.ux + a.uy * b.uy;
                if (std::abs(dot) < cos_tol) continue;

                double nx = -a.uy;
                double ny = a.ux;

                // Distance perpendiculaire des deux extremites de B par rapport a la droite de A
                double d0 = std::abs((b.x0 - a.x0) * nx + (b.y0 - a.y0) * ny);
                double d1 = std::abs((b.x1 - a.x0) * nx + (b.y1 - a.y0) * ny);
                if (std::max(d0, d1) > perp_tol_px) continue;

                // Projection longitudinale sur le repere de A
                double sa0 = 0.0;
                double sa1 = a.len;
                double sb0 = (b.x0 - a.x0) * a.ux + (b.y0 - a.y0) * a.uy;
                double sb1 = (b.x1 - a.x0) * a.ux + (b.y1 - a.y0) * a.uy;
                if (sb0 > sb1) std::swap(sb0, sb1);

                // Ecart longitudinal (> 0 si trou entre les deux, <= 0 s'ils se chevauchent)
                double gap = std::max(sa0, sb0) - std::min(sa1, sb1);
                if (gap > max_collinear_gap_px) continue;

                // Fusion ponderee par les longueurs pour stabiliser l'axe
                double w_a = a.len;
                double w_b = b.len;
                double w_sum = w_a + w_b;

                double cx = (w_a * 0.5 * (a.x0 + a.x1) + w_b * 0.5 * (b.x0 + b.x1)) / w_sum;
                double cy = (w_a * 0.5 * (a.y0 + a.y1) + w_b * 0.5 * (b.y0 + b.y1)) / w_sum;

                double ub_x = (dot >= 0.0) ? b.ux : -b.ux;
                double ub_y = (dot >= 0.0) ? b.uy : -b.uy;
                double ux = w_a * a.ux + w_b * ub_x;
                double uy = w_a * a.uy + w_b * ub_y;
                double unorm = std::hypot(ux, uy);
                if (unorm < 1e-9) continue;
                ux /= unorm;
                uy /= unorm;

                double p_a0 = (a.x0 - cx) * ux + (a.y0 - cy) * uy;
                double p_a1 = (a.x1 - cx) * ux + (a.y1 - cy) * uy;
                double p_b0 = (b.x0 - cx) * ux + (b.y0 - cy) * uy;
                double p_b1 = (b.x1 - cx) * ux + (b.y1 - cy) * uy;

                double s_min = std::min({p_a0, p_a1, p_b0, p_b1});
                double s_max = std::max({p_a0, p_a1, p_b0, p_b1});

                a.x0 = cx + s_min * ux;
                a.y0 = cy + s_min * uy;
                a.x1 = cx + s_max * ux;
                a.y1 = cy + s_max * uy;
                refresh_segment(a);

                b.active = false;
                merged_any = true;
            }
        }
    }

    int32_t out_count = 0;
    for (int32_t i = 0; i < m && out_count < max_out_segments; ++i) {
        if (!segs[i].active) continue;
        double* dst = out_segments + (out_count * 4);
        dst[0] = segs[i].x0;
        dst[1] = segs[i].y0;
        dst[2] = segs[i].x1;
        dst[3] = segs[i].y1;
        ++out_count;
    }

    return out_count;
}

/**
 * 4. Appariement O(M^2) des contours paralleles pour former les murs en double trait.
 */
FASTWALL_EXPORT int32_t pair_all_wall_faces_c(
    const double* __restrict segments,
    int32_t n_segments,
    double min_width_px,
    double max_width_px,
    double angle_tol_deg,
    double min_overlap_ratio,
    double max_width_variation,
    WallPairC* __restrict out_pairs,
    int32_t max_out_pairs
) {
    if (n_segments < 2 || max_out_pairs <= 0) return 0;

    struct SegMeta {
        double x0, y0, x1, y1;
        double ux, uy, len;
        uint8_t valid;
    };

    std::vector<SegMeta> meta(n_segments);
    for (int32_t i = 0; i < n_segments; ++i) {
        const double* s = segments + (i * 4);
        double dx = s[2] - s[0];
        double dy = s[3] - s[1];
        double l = std::hypot(dx, dy);
        if (l < 1.0) {
            meta[i].valid = 0;
        } else {
            meta[i] = {s[0], s[1], s[2], s[3], dx / l, dy / l, l, 1};
        }
    }

    double cos_tol = std::cos(angle_tol_deg * kDegToRad);
    int32_t pair_count = 0;

    for (int32_t i = 0; i < n_segments; ++i) {
        const SegMeta& a = meta[i];
        if (!a.valid) continue;

        for (int32_t j = i + 1; j < n_segments; ++j) {
            const SegMeta& b = meta[j];
            if (!b.valid) continue;

            double dot = a.ux * b.ux + a.uy * b.uy;
            double abs_dot = std::abs(dot);
            if (abs_dot < cos_tol) continue;

            double ub_x = (dot >= 0.0) ? b.ux : -b.ux;
            double ub_y = (dot >= 0.0) ? b.uy : -b.uy;

            double ux = a.ux + ub_x;
            double uy = a.uy + ub_y;
            double unorm = std::hypot(ux, uy);
            if (unorm < 1e-9) continue;
            ux /= unorm;
            uy /= unorm;
            double nx = -uy;
            double ny = ux;

            double approx_sep = std::abs((b.x0 - a.x0) * nx + (b.y0 - a.y0) * ny);
            if (approx_sep < min_width_px - max_width_variation ||
                approx_sep > max_width_px + max_width_variation) {
                continue;
            }

            double sa0 = a.x0 * ux + a.y0 * uy;
            double sa1 = a.x1 * ux + a.y1 * uy;
            double sb0 = b.x0 * ux + b.y0 * uy;
            double sb1 = b.x1 * ux + b.y1 * uy;

            double sa_min = std::min(sa0, sa1), sa_max = std::max(sa0, sa1);
            double sb_min = std::min(sb0, sb1), sb_max = std::max(sb0, sb1);

            double start = std::max(sa_min, sb_min);
            double end = std::min(sa_max, sb_max);
            double overlap = end - start;
            double shorter_span = std::min(sa_max - sa_min, sb_max - sb_min);

            if (overlap <= 0.0 || shorter_span <= 0.0) continue;
            double overlap_ratio = overlap / shorter_span;
            if (overlap_ratio < min_overlap_ratio) continue;

            auto interp_normal = [&](double x0, double y0, double x1, double y1, double s0, double s1, double pos) {
                double denom = s1 - s0;
                if (std::abs(denom) < 1e-9) return x0 * nx + y0 * ny;
                double frac = (pos - s0) / denom;
                double px = x0 + frac * (x1 - x0);
                double py = y0 + frac * (y1 - y0);
                return px * nx + py * ny;
            };

            double oa0 = interp_normal(a.x0, a.y0, a.x1, a.y1, sa0, sa1, start);
            double oa1 = interp_normal(a.x0, a.y0, a.x1, a.y1, sa0, sa1, end);
            double ob0 = interp_normal(b.x0, b.y0, b.x1, b.y1, sb0, sb1, start);
            double ob1 = interp_normal(b.x0, b.y0, b.x1, b.y1, sb0, sb1, end);

            double sw0 = ob0 - oa0;
            double sw1 = ob1 - oa1;
            if (sw0 * sw1 <= 0.0) continue;

            double w0 = std::abs(sw0);
            double w1 = std::abs(sw1);
            double w_min = std::min(w0, w1);
            double w_max = std::max(w0, w1);

            if (w_min < min_width_px || w_max > max_width_px) continue;
            if ((w_max - w_min) > max_width_variation) continue;

            if (pair_count < max_out_pairs) {
                double c_off0 = 0.5 * (oa0 + ob0);
                double c_off1 = 0.5 * (oa1 + ob1);

                WallPairC& dst = out_pairs[pair_count++];
                dst.cx1 = start * ux + c_off0 * nx;
                dst.cy1 = start * uy + c_off0 * ny;
                dst.cx2 = end * ux + c_off1 * nx;
                dst.cy2 = end * uy + c_off1 * ny;

                dst.ax1 = start * ux + oa0 * nx;
                dst.ay1 = start * uy + oa0 * ny;
                dst.ax2 = end * ux + oa1 * nx;
                dst.ay2 = end * uy + oa1 * ny;

                dst.bx1 = start * ux + ob0 * nx;
                dst.by1 = start * uy + ob0 * ny;
                dst.bx2 = end * ux + ob1 * nx;
                dst.by2 = end * uy + ob1 * ny;

                dst.width_px = 0.5 * (w0 + w1);
                dst.overlap_px = overlap;
                dst.overlap_ratio = overlap_ratio;
                dst.angle_diff_deg = std::acos(std::clamp(abs_dot, -1.0, 1.0)) * kRadToDeg;
                dst.idx_a = i;
                dst.idx_b = j;
            }
        }
    }

    return pair_count;
}

} // extern "C"
