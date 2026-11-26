// Private PCM-to-text bridge. No network, user commands, file output or audio capture.
// Compiled against whisper.cpp v1.9.5; loaded DLLs are verified by the parent.
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "whisper.h"

template<class T> T load(HMODULE module, const char *name) {
    auto fn = reinterpret_cast<T>(GetProcAddress(module, name));
    if (!fn) throw std::runtime_error("runtime interface mismatch");
    return fn;
}
#define API(name) auto name##_fn = load<decltype(&name)>(module, #name)

static std::string quote(const std::string &text) {
    std::string result = "\"";
    const char hex[] = "0123456789abcdef";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') { result += '\\'; result += c; }
        else if (c < 32) { result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15]; }
        else result += c;
    }
    return result + "\"";
}

static whisper_model_loader loader(FILE *file) {
    whisper_model_loader l{};
    l.context = file;
    l.read = [](void *ctx, void *out, size_t size) { return fread(out, 1, size, static_cast<FILE *>(ctx)); };
    l.eof = [](void *ctx) { return feof(static_cast<FILE *>(ctx)) != 0; };
    l.close = [](void *ctx) { fclose(static_cast<FILE *>(ctx)); };
    return l;
}

// Both approved English models share a vocabulary. Score the same prefix
// against the same PCM, so disagreements are resolved before committing text.
struct JointDecoder {
    whisper_context *base = nullptr;
    decltype(&whisper_decode) decode;
    decltype(&whisper_get_logits) logits;
    int vocabulary;
    whisper_token prompt[2];
    int consumed = 0;
    bool failed = false;
    unsigned tiebreaks = 0;

    static void mix(whisper_context *, whisper_state *, const whisper_token_data *tokens,
                    int count, float *scores, void *user) {
        auto &joint = *static_cast<JointDecoder *>(user);
        if (joint.failed) return;
        int row = 0;
        if (!count) {
            joint.failed = joint.decode(joint.base, joint.prompt, 2, 0, 4) != 0;
            joint.consumed = 0;
            row = 1;  // whisper_decode returns one logit row per prompt token.
        } else if (count == joint.consumed + 1) {
            joint.failed = joint.decode(joint.base, &tokens[count - 1].id, 1, 2 + joint.consumed, 4) != 0;
            joint.consumed = count;
        } else {
            joint.failed = true;
        }
        if (joint.failed) return;
        const float *other = joint.logits(joint.base) + row * joint.vocabulary;
        // Small leads. Base can break a near tie only if it independently
        // picks Small's runner-up and favors it by at least three to one.
        int first = -1, second = -1, base_first = -1;
        for (int i = 0; i < joint.vocabulary; ++i) {
            if (!std::isfinite(scores[i])) continue;  // Keep Small's token exclusions.
            if (base_first < 0 || other[i] > other[base_first]) base_first = i;
            if (first < 0 || scores[i] > scores[first]) {
                second = first;
                first = i;
            } else if (second < 0 || scores[i] > scores[second]) {
                second = i;
            }
        }
        if (second >= 0 && base_first == second && scores[first] - scores[second] < std::log(2.0f)
                && other[second] - other[first] >= std::log(3.0f)) {
            std::swap(scores[first], scores[second]);
            ++joint.tiebreaks;
        }
    }
};

int wmain(int argc, wchar_t **argv) {
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    try {
        if (argc != 4 && argc != 5) return 2;
        HMODULE module = LoadLibraryExW(argv[1], nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (!module) throw std::runtime_error("runtime unavailable");
        API(whisper_log_set); API(whisper_context_default_params); API(whisper_init_with_params);
        API(whisper_full_default_params); API(whisper_full); API(whisper_full_n_segments);
        API(whisper_model_n_audio_layer); API(whisper_model_n_audio_ctx);
        API(whisper_pcm_to_mel); API(whisper_encode); API(whisper_decode); API(whisper_get_logits);
        API(whisper_n_vocab); API(whisper_token_sot); API(whisper_token_not);
        API(whisper_full_get_segment_text); API(whisper_free); API(whisper_vad_default_context_params);
        API(whisper_vad_init_with_params); API(whisper_vad_detect_speech); API(whisper_vad_n_probs);
        API(whisper_vad_probs); API(whisper_vad_free);
        whisper_log_set_fn([](enum ggml_log_level, const char *, void *) {}, nullptr);
        // The release uses dynamically selected CPU backends. Library clients
        // must register them before asking Whisper to allocate its model.
        load<decltype(&ggml_backend_load_all)>(GetModuleHandleW(L"ggml.dll"), "ggml_backend_load_all")();
        auto cp = whisper_context_default_params_fn(); cp.use_gpu = false;
        FILE *model = _wfopen(argv[2], L"rb");
        if (!model) throw std::runtime_error("model unavailable");
        auto ml = loader(model);
        auto *ctx = whisper_init_with_params_fn(&ml, cp);
        if (!ctx) throw std::runtime_error("model could not load");
        JointDecoder joint{nullptr, whisper_decode_fn, whisper_get_logits_fn, whisper_n_vocab_fn(ctx),
                           {whisper_token_sot_fn(ctx), whisper_token_not_fn(ctx)}};
        if (argc == 5) {
            FILE *base_file = _wfopen(argv[4], L"rb");
            if (!base_file) throw std::runtime_error("companion model unavailable");
            auto base_loader = loader(base_file);
            joint.base = whisper_init_with_params_fn(&base_loader, cp);
            if (!joint.base || joint.vocabulary != whisper_n_vocab_fn(joint.base)
                    || joint.prompt[0] != whisper_token_sot_fn(joint.base)
                    || joint.prompt[1] != whisper_token_not_fn(joint.base)) {
                throw std::runtime_error("incompatible companion model");
            }
        }
        FILE *vad_file = _wfopen(argv[3], L"rb");
        if (!vad_file) throw std::runtime_error("speech detector unavailable");
        auto vl = loader(vad_file);
        auto vp = whisper_vad_default_context_params_fn(); vp.use_gpu = false; vp.n_threads = 2;
        auto *vad = whisper_vad_init_with_params_fn(&vl, vp);
        if (!vad) throw std::runtime_error("speech detector could not load");
        auto params = whisper_full_default_params_fn(WHISPER_SAMPLING_GREEDY);
        params.n_threads = 4; params.language = "en"; params.translate = false;
        params.no_context = true; params.no_timestamps = true;
        params.print_realtime = params.print_progress = params.print_timestamps = params.print_special = false;
        params.suppress_blank = params.suppress_nst = true;
        params.temperature = params.temperature_inc = 0.0f;
        params.greedy.best_of = 1;
        const bool larger_model = whisper_model_n_audio_layer_fn(ctx) > 6;
        const int model_context = whisper_model_n_audio_ctx_fn(ctx);
        std::cout << "{\"event\":\"ready\"}\n" << std::flush;
        // uint32 sample count followed by that many little-endian float32 samples.
        // High bit requests detection only. Bit 30 requests a joint final.
        uint32_t header;
        while (std::cin.read(reinterpret_cast<char *>(&header), 4)) {
            if (!header) break;
            bool detect_only = (header & 0x80000000u) != 0;
            bool combine = (header & 0x40000000u) != 0;
            if (combine && (!joint.base || detect_only)) throw std::runtime_error("invalid recognition mode");
            uint32_t count = header & 0x3fffffffu;
            if (count > 480000 || count < 512) throw std::runtime_error("invalid audio frame");
            std::vector<float> pcm(count);
            if (!std::cin.read(reinterpret_cast<char *>(pcm.data()), count * 4)) break;
            for (float x : pcm) if (!std::isfinite(x) || std::abs(x) > 1.01f) throw std::runtime_error("invalid samples");
            if (!whisper_vad_detect_speech_fn(vad, pcm.data(), static_cast<int>(count))) throw std::runtime_error("speech detection failed");
            int n = whisper_vad_n_probs_fn(vad);
            const float *probs = whisper_vad_probs_fn(vad);
            bool speech = false;
            for (int i = 0; i < n; ++i) speech |= probs[i] >= 0.5f;
            std::string text;
            joint.tiebreaks = 0;
            if (!detect_only && speech) {
                // Each encoder position covers 20 ms. Include the entire
                // utterance plus padding without encoding 30 s for every preview.
                if (larger_model) {
                    const int positions = (static_cast<int>(count) + 319) / 320 + 50;
                    params.audio_ctx = std::min(model_context, std::max(384, (positions + 31) / 32 * 32));
                }
                joint.failed = false;
                params.logits_filter_callback = combine ? JointDecoder::mix : nullptr;
                params.logits_filter_callback_user_data = combine ? &joint : nullptr;
                params.single_segment = combine;
                if (combine && (whisper_pcm_to_mel_fn(joint.base, pcm.data(), count, 4) != 0
                                || whisper_encode_fn(joint.base, 0, 4) != 0)) {
                    throw std::runtime_error("companion encoding failed");
                }
                if (whisper_full_fn(ctx, params, pcm.data(), static_cast<int>(count)) || joint.failed)
                    throw std::runtime_error("transcription failed");
                for (int i = 0; i < whisper_full_n_segments_fn(ctx); ++i) text += whisper_full_get_segment_text_fn(ctx, i);
                if (text.size() > 32000) throw std::runtime_error("transcript too large");
            }
            std::cout << "{\"event\":\"result\",\"speech\":" << (speech ? "true" : "false")
                      << ",\"base_tiebreaks\":" << joint.tiebreaks << ",\"text\":" << quote(text) << "}\n" << std::flush;
        }
        whisper_vad_free_fn(vad);
        if (joint.base) whisper_free_fn(joint.base);
        whisper_free_fn(ctx); FreeLibrary(module);
        return 0;
    } catch (...) {
        std::cout << "{\"event\":\"error\",\"message\":\"Local speech runtime failed. Check the model installation.\"}\n" << std::flush;
        return 1;
    }
}
