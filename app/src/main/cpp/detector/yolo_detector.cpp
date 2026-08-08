#include "yolo_detector.h"
#include <chrono>
#include <algorithm>
#include <array>
#include <cmath>
#include <vector>
#include <android/hardware_buffer_jni.h>

namespace ESP {

YoloDetector::YoloDetector()
    : vulkanDevice_(nullptr)
    , initialized_(false)
    , screenWidth_(1080)
    , screenHeight_(2400)
    , confidenceThreshold_(Config::DEFAULT_CONFIDENCE_THRESHOLD) {
    LOGD("YoloDetector created");
}

YoloDetector::~YoloDetector() {
    shutdown();
}

bool YoloDetector::initialize(AAssetManager* assetManager,
                              int screenWidth,
                              int screenHeight,
                              const char* modelParamPath,
                              const char* modelBinPath) {
    if (initialized_) {
        LOGW("YoloDetector already initialized");
        return true;
    }
    
    if (!assetManager) {
        LOGE("AssetManager is null");
        return false;
    }
    
    screenWidth_.store(screenWidth, std::memory_order_relaxed);
    screenHeight_.store(screenHeight, std::memory_order_relaxed);
    LOGI("Initializing YoloDetector for screen %dx%d", screenWidth, screenHeight);
    
    // Initialize Vulkan if available
    int gpuCount = ncnn::get_gpu_count();
    LOGI("NCNN GPU count: %d", gpuCount);
    
    if (gpuCount > 0 && Config::NCNN_USE_VULKAN_COMPUTE) {
        vulkanDevice_ = ncnn::get_gpu_device(0);
        if (vulkanDevice_) {
            net_.set_vulkan_device(vulkanDevice_);
            LOGI("Vulkan device set: %s", vulkanDevice_->info.device_name());
        } else {
            LOGW("Failed to get Vulkan device, falling back to CPU");
        }
    } else {
        LOGI("Vulkan not available or disabled, using CPU");
    }
    
    // Configure NCNN options for Adreno 660
    net_.opt.use_vulkan_compute = (vulkanDevice_ != nullptr);
    net_.opt.use_fp16_packed = Config::NCNN_USE_FP16_PACKED;
    net_.opt.use_fp16_storage = Config::NCNN_USE_FP16_STORAGE;
    net_.opt.use_fp16_arithmetic = Config::NCNN_USE_FP16_ARITHMETIC;
    net_.opt.use_packing_layout = Config::NCNN_USE_PACKING_LAYOUT;
    net_.opt.use_sgemm_convolution = true;  // Optimized matrix multiplication
    net_.opt.use_winograd_convolution = true;  // Fast convolution algorithm
    net_.opt.lightmode = Config::NCNN_LIGHT_MODE;
    net_.opt.num_threads = Config::NCNN_NUM_THREADS;
    
    LOGI("NCNN options: vulkan=%d, fp16_storage=%d, fp16_arith=%d, threads=%d",
         net_.opt.use_vulkan_compute, net_.opt.use_fp16_storage,
         net_.opt.use_fp16_arithmetic, net_.opt.num_threads);
    
    int ret = -1;
    if (modelParamPath && modelBinPath && modelParamPath[0] != '\0' && modelBinPath[0] != '\0') {
        LOGI("Trying local model files: %s / %s", modelParamPath, modelBinPath);
        ret = net_.load_param(modelParamPath);
        if (ret == 0) {
            ret = net_.load_model(modelBinPath);
        }

        if (ret == 0) {
            LOGI("Loaded model from local storage");
        } else {
            LOGW("Local model load failed (error %d), falling back to assets", ret);
        }
    }

    if (ret != 0) {
        ret = net_.load_param(assetManager, Config::MODEL_PARAM_FILE);
        if (ret != 0) {
            LOGE("Failed to load model param: %s (error %d)", Config::MODEL_PARAM_FILE, ret);
            return false;
        }
        LOGI("Loaded model param: %s", Config::MODEL_PARAM_FILE);

        ret = net_.load_model(assetManager, Config::MODEL_BIN_FILE);
        if (ret != 0) {
            LOGE("Failed to load model bin: %s (error %d)", Config::MODEL_BIN_FILE, ret);
            return false;
        }
        LOGI("Loaded model bin: %s", Config::MODEL_BIN_FILE);
    }

    // Cache input/output blob names when available to avoid repeated lookup warnings
#if NCNN_STRING
    const auto& inputNames = net_.input_names();
    if (!inputNames.empty() && inputNames[0]) {
        inputBlobName_ = inputNames[0];
    }

    const auto& outputNames = net_.output_names();
    if (!outputNames.empty() && outputNames[0]) {
        outputBlobName_ = outputNames[0];
    }
#endif
    
    // Pre-allocate input mat
    inputMat_.create(Config::MODEL_INPUT_SIZE, Config::MODEL_INPUT_SIZE, 3);
    
    initialized_ = true;
    LOGI("YoloDetector initialized successfully");
    return true;
}

void YoloDetector::shutdown() {
    if (!initialized_) {
        return;
    }
    
    LOGI("Shutting down YoloDetector");
    net_.clear();
    vulkanDevice_ = nullptr;
    initialized_ = false;
}

bool YoloDetector::detect(AHardwareBuffer* buffer, DetectionResult& result) {
    if (!initialized_) {
        LOGE("Detector not initialized");
        return false;
    }

    if (!buffer) {
        LOGE("Hardware buffer is null");
        return false;
    }

    result.clear();

    auto startTime = std::chrono::high_resolution_clock::now();

    // ---- Full-screen tiled detection -------------------------------------
    // The whole capture frame is scanned with overlapping square tiles, each
    // resized into the model input. This covers the ENTIRE screen while keeping
    // a gentle downscale (e.g. 480 -> 256), which is far more accurate than
    // stretching the whole 1280x720 frame into a single 256x256 square.

    AHardwareBuffer_Desc desc;
    AHardwareBuffer_describe(buffer, &desc);

    if (desc.format != AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM) {
        LOGE("Unsupported buffer format: %d", desc.format);
        return false;
    }

    void* pixels = nullptr;
    int lockResult = AHardwareBuffer_lock(buffer, AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN,
                                          -1, nullptr, &pixels);
    if (lockResult != 0 || !pixels) {
        LOGE("Failed to lock hardware buffer: %d", lockResult);
        return false;
    }

    const uint8_t* srcPixels = static_cast<const uint8_t*>(pixels);
    int srcWidth = static_cast<int>(desc.width);
    int srcHeight = static_cast<int>(desc.height);
    int srcStride = static_cast<int>(desc.stride) * 4;  // RGBA = 4 bytes per pixel

    currentCaptureWidth_ = srcWidth;
    currentCaptureHeight_ = srcHeight;
    currentCropX_ = 0;
    currentCropY_ = 0;
    fullFrame_ = true;

    float screenW = static_cast<float>(screenWidth_.load(std::memory_order_relaxed));
    float screenH = static_cast<float>(screenHeight_.load(std::memory_order_relaxed));
    float captureToScreenX = (srcWidth > 0) ? (screenW / static_cast<float>(srcWidth)) : 1.0f;
    float captureToScreenY = (srcHeight > 0) ? (screenH / static_cast<float>(srcHeight)) : 1.0f;

    const int tile = Config::FULL_FRAME_TILE_SIZE;
    const int step = std::max(1, tile - Config::FULL_FRAME_TILE_OVERLAP);
    const float modelSize = static_cast<float>(Config::MODEL_INPUT_SIZE);

    const std::array<float, 3> meanVals = {0.0f, 0.0f, 0.0f};
    const std::array<float, 3> normVals = {1.0f / 255.0f, 1.0f / 255.0f, 1.0f / 255.0f};

    float confThreshold = confidenceThreshold_.load(std::memory_order_relaxed);

    DetectionArray combined;

    // Build uniform, full-size tile origins. Naively walking `startX += step`
    // and clamping the *size* at the right/bottom edge produced thin strips
    // (e.g. 128x336) that were then stretched into a 256x256 square input.
    // That extreme aspect distortion is a major source of phantom detections
    // ("enemy found where there is none"). Instead we clamp the *origin* so
    // every tile keeps the same square shape and the last one sits flush
    // against the edge; overlap absorbs the difference.
    const int tileW = std::min(tile, srcWidth);
    const int tileH = std::min(tile, srcHeight);

    std::vector<int> xOrigins;
    for (int x = 0;; x += step) {
        const int sx = std::min(x, srcWidth - tileW);
        if (!xOrigins.empty() && sx == xOrigins.back()) break;
        xOrigins.push_back(sx);
        if (sx + tileW >= srcWidth) break;
    }

    std::vector<int> yOrigins;
    for (int y = 0;; y += step) {
        const int sy = std::min(y, srcHeight - tileH);
        if (!yOrigins.empty() && sy == yOrigins.back()) break;
        yOrigins.push_back(sy);
        if (sy + tileH >= srcHeight) break;
    }

    // Precomputed, identical for every tile now that they are all the same size.
    const float scaleX = (static_cast<float>(tileW) / modelSize) * captureToScreenX;
    const float scaleY = (static_cast<float>(tileH) / modelSize) * captureToScreenY;

    for (int startY : yOrigins) {
        for (int startX : xOrigins) {
            const uint8_t* tileStart = srcPixels
                + static_cast<size_t>(startY) * static_cast<size_t>(srcStride)
                + static_cast<size_t>(startX) * 4u;

            // Resize this tile (tileW x tileH) directly into the model input.
            inputMat_ = ncnn::Mat::from_pixels_resize(
                tileStart,
                ncnn::Mat::PIXEL_RGBA2RGB,
                tileW, tileH,
                srcStride,
                Config::MODEL_INPUT_SIZE, Config::MODEL_INPUT_SIZE
            );
            inputMat_.substract_mean_normalize(meanVals.data(), normVals.data());

            ncnn::Mat output;
            if (runInference(inputMat_, output)) {
                // Map this tile's model-space boxes into screen space.
                const float offsetX = static_cast<float>(startX) * captureToScreenX;
                const float offsetY = static_cast<float>(startY) * captureToScreenY;
                decodeBoxes(output, combined, scaleX, scaleY, offsetX, offsetY, confThreshold);
            }
        }
    }

    int unlockResult = AHardwareBuffer_unlock(buffer, nullptr);
    if (unlockResult != 0) {
        LOGW("AHardwareBuffer_unlock failed: %d", unlockResult);
    }

    // One global NMS across all tiles so overlapping detections merge cleanly.
    if (combined.size() > 1) {
        applyNMS(combined);
    }
    result.boxes = combined;

    auto endTime = std::chrono::high_resolution_clock::now();
    result.inferenceTimeMs = std::chrono::duration<float, std::milli>(endTime - startTime).count();

    // Store result thread-safely
    {
        std::scoped_lock lock(resultMutex_);
        latestResult_ = result;
    }

    LOGP("Detection: %d boxes in %.2f ms", result.boxes.size(), result.inferenceTimeMs);

    return true;
}

bool YoloDetector::detect(AHardwareBuffer* buffer, DetectionResult& result, int dynamicCropSize,
                          bool fullFrame) {
    if (!initialized_) {
        LOGE("Detector not initialized");
        return false;
    }
    
    if (!buffer) {
        LOGE("Hardware buffer is null");
        return false;
    }
    
    result.clear();
    
    auto startTime = std::chrono::high_resolution_clock::now();
    
    // Preprocess: extract pixels, center crop, resize into persistent buffer
    if (!preprocess(buffer, inputMat_, dynamicCropSize, fullFrame)) {
        LOGE("Preprocessing failed");
        return false;
    }

    // Run inference
    ncnn::Mat output;
    if (!runInference(inputMat_, output)) {
        LOGE("Inference failed");
        return false;
    }

    // Post-process: decode boxes, NMS, coordinate mapping
    postprocess(output, result, dynamicCropSize, fullFrame);
    
    auto endTime = std::chrono::high_resolution_clock::now();
    result.inferenceTimeMs = std::chrono::duration<float, std::milli>(endTime - startTime).count();
    
    // Store result thread-safely
    {
        std::scoped_lock lock(resultMutex_);
        latestResult_ = result;
    }
    
    LOGP("Detection: %d boxes in %.2f ms", result.boxes.size(), result.inferenceTimeMs);
    
    return true;
}

DetectionResult YoloDetector::getResult() const {
    std::scoped_lock lock(resultMutex_);
    return latestResult_;
}

bool YoloDetector::preprocess(AHardwareBuffer* buffer, ncnn::Mat& inputMat, int cropSize, bool fullFrame) {
    // Get hardware buffer description
    AHardwareBuffer_Desc desc;
    AHardwareBuffer_describe(buffer, &desc);
    
    if (desc.format != AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM) {
        LOGE("Unsupported buffer format: %d", desc.format);
        return false;
    }
    
    // Lock buffer for CPU read
    void* pixels = nullptr;
    int result = AHardwareBuffer_lock(buffer, AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN, 
                                       -1, nullptr, &pixels);
    if (result != 0 || !pixels) {
        LOGE("Failed to lock hardware buffer: %d", result);
        return false;
    }
    
    const uint8_t* srcPixels = static_cast<const uint8_t*>(pixels);
    int srcWidth = static_cast<int>(desc.width);
    int srcHeight = static_cast<int>(desc.height);
    int srcStride = static_cast<int>(desc.stride) * 4;  // RGBA = 4 bytes per pixel

    currentCaptureWidth_ = srcWidth;
    currentCaptureHeight_ = srcHeight;
    
    // Clamp crop size
    currentCropX_ = 0;
    currentCropY_ = 0;
    fullFrame_ = fullFrame;

    int actualW, actualH;
    if (fullFrame) {
        // Whole-screen detection: resize the entire capture frame into the
        // square model input. Coordinate mapping is handled in postprocess().
        actualW = srcWidth;
        actualH = srcHeight;
    } else {
        int actualCropSize = std::min(cropSize, std::min(srcWidth, srcHeight));
        actualCropSize = std::max(32, actualCropSize);

        // Center crop coordinates
        int cropX = (srcWidth - actualCropSize) / 2;
        int cropY = (srcHeight - actualCropSize) / 2;
        currentCropX_ = std::max(0, std::min(cropX, srcWidth - actualCropSize));
        currentCropY_ = std::max(0, std::min(cropY, srcHeight - actualCropSize));
        actualW = actualCropSize;
        actualH = actualCropSize;
    }

    // OPTIMIZED: Direct resize from RGBA buffer to model input (skip intermediate crop)
    const uint8_t* srcStart = srcPixels + currentCropY_ * srcStride + currentCropX_ * 4;

    // Use NCNN's optimized from_pixels_resize (handles RGBA->RGB + resize in one pass)
    inputMat = ncnn::Mat::from_pixels_resize(
        srcStart,
        ncnn::Mat::PIXEL_RGBA2RGB,
        actualW, actualH,
        srcStride,  // stride in bytes
        Config::MODEL_INPUT_SIZE, Config::MODEL_INPUT_SIZE
    );
    
    int unlockResult = AHardwareBuffer_unlock(buffer, nullptr);
    if (unlockResult != 0) {
        LOGW("AHardwareBuffer_unlock failed: %d", unlockResult);
    }
    
    // Normalize: scale from [0, 255] to [0, 1]
    const std::array<float, 3> meanVals = {0.0f, 0.0f, 0.0f};
    const std::array<float, 3> normVals = {1.0f / 255.0f, 1.0f / 255.0f, 1.0f / 255.0f};
    inputMat.substract_mean_normalize(meanVals.data(), normVals.data());
    
    return true;
}

bool YoloDetector::runInference(const ncnn::Mat& input, ncnn::Mat& output) {
    ncnn::Extractor ex = net_.create_extractor();

    // Set input using cached name if available, otherwise fallback to index 0
    int ret = -1;
    if (!useInputIndex_ && !inputBlobName_.empty()) {
        ret = ex.input(inputBlobName_.c_str(), input);
    }

    if (ret != 0) {
        useInputIndex_ = true;
        ret = ex.input(0, input);
    }

    if (ret != 0) {
        LOGE("Failed to set input: %d", ret);
        return false;
    }

    // Get output using cached name if available, otherwise fallback to index 0
    ret = -1;
    if (!useOutputIndex_ && !outputBlobName_.empty()) {
        ret = ex.extract(outputBlobName_.c_str(), output);
    }

    if (ret != 0) {
        useOutputIndex_ = true;
        ret = ex.extract(0, output);
    }

    if (ret != 0) {
        LOGE("Failed to extract output: %d", ret);
        return false;
    }

    return true;
}

namespace {

/// Reject boxes that cannot plausibly correspond to a real target.
/// Degenerate, frame-sized, off-frame or absurdly elongated boxes are the
/// classic signature of a phantom detection fired on background noise.
inline bool IsPlausibleModelBox(float cx, float cy, float w, float h, float modelSize) {
    if (!std::isfinite(cx) || !std::isfinite(cy) ||
        !std::isfinite(w) || !std::isfinite(h)) {
        return false;
    }
    if (w < 2.0f || h < 2.0f) return false;
    if (w > modelSize * 1.05f || h > modelSize * 1.05f) return false;

    const float margin = modelSize * 0.15f;
    if (cx < -margin || cx > modelSize + margin) return false;
    if (cy < -margin || cy > modelSize + margin) return false;

    const float ratio = w / h;
    if (ratio > 8.0f || ratio < 0.125f) return false;

    return true;
}

} // namespace

void YoloDetector::decodeBoxes(const ncnn::Mat& output, DetectionArray& out,
                                float scaleX, float scaleY, float offsetX, float offsetY,
                                float confThreshold) {
    int numBoxes, numValues;
    bool transposed = false;

    // Determine the per-box channel dimension robustly. YOLOv26n-style heads
    // emit 5 channels (x,y,w,h,conf) or 6 (x,y,w,h,obj,conf) per box. Locking
    // onto the dimension whose size is 5 or 6 (the channel count) is safer than
    // the old "smaller dimension wins" heuristic, which silently transposes the
    // tensor - and therefore reads garbage - for any unusual output shape.
    if (output.w == 5 || output.w == 6) {
        numBoxes = output.h;
        numValues = output.w;
        transposed = false;
    } else if (output.h == 5 || output.h == 6) {
        numBoxes = output.w;
        numValues = output.h;
        transposed = true;
    } else {
        numBoxes = output.h;
        numValues = output.w;
        if (numBoxes < numValues) {
            transposed = true;
            std::swap(numBoxes, numValues);
        }
    }

    float modelSize = static_cast<float>(Config::MODEL_INPUT_SIZE);

    int numClasses = numValues - 4;
    int classOffset = 4;
    float objectness = 1.0f;

    // Auto-detect YOLO version (extra objectness channel present).
    if (numClasses == Config::NUM_CLASSES + 1) {
        classOffset = 5;
        numClasses -= 1;
    }
    if (numClasses < 1) numClasses = 1;

    if (transposed) {
        const float* row0 = output.row(0);
        const float* row1 = output.row(1);
        const float* row2 = output.row(2);
        const float* row3 = output.row(3);
        const float* rowObj = (classOffset > 4) ? output.row(4) : nullptr;

        for (int i = 0; i < numBoxes; ++i) {
            if (out.full()) break;

            float maxClassProb = 0.0f;
            int bestClassId = 0;

            if (classOffset > 4) objectness = rowObj[i];

            for (int c = 0; c < numClasses; ++c) {
                float prob = output.row(classOffset + c)[i];
                prob *= objectness;
                if (prob > maxClassProb) {
                    maxClassProb = prob;
                    bestClassId = c;
                }
            }

            if (!std::isfinite(maxClassProb)) continue;
            if (maxClassProb < confThreshold) continue;
            if (Config::FILTER_ENEMY_ONLY && bestClassId != Config::ENEMY_CLASS_ID) continue;

            float xCenter = row0[i];
            float yCenter = row1[i];
            float width = row2[i];
            float height = row3[i];

            if (xCenter <= 1.5f) {
                xCenter *= modelSize;
                yCenter *= modelSize;
                width *= modelSize;
                height *= modelSize;
            }

            if (!IsPlausibleModelBox(xCenter, yCenter, width, height, modelSize)) continue;

            float halfW = width * 0.5f;
            float halfH = height * 0.5f;

            float boxX = (xCenter - halfW) * scaleX + offsetX;
            float boxY = (yCenter - halfH) * scaleY + offsetY;
            float boxW = width * scaleX;
            float boxH = height * scaleY;

            if (boxW <= 0.0f || boxH <= 0.0f) continue;

            BoundingBox box;
            box.x = boxX;
            box.y = boxY;
            box.width = boxW;
            box.height = boxH;
            box.confidence = maxClassProb;
            box.classId = bestClassId;

            out.push(box);
        }
    } else {
        for (int i = 0; i < numBoxes; ++i) {
            if (out.full()) break;

            const float* values = output.row(i);

            float maxClassProb = 0.0f;
            int bestClassId = 0;

            if (classOffset > 4) objectness = values[4];

            for (int c = 0; c < numClasses; ++c) {
                float prob = values[classOffset + c];
                prob *= objectness;
                if (prob > maxClassProb) {
                    maxClassProb = prob;
                    bestClassId = c;
                }
            }

            if (!std::isfinite(maxClassProb)) continue;
            if (maxClassProb < confThreshold) continue;
            if (Config::FILTER_ENEMY_ONLY && bestClassId != Config::ENEMY_CLASS_ID) continue;

            float xCenter = values[0];
            float yCenter = values[1];
            float width = values[2];
            float height = values[3];

            if (xCenter <= 1.5f) {
                xCenter *= modelSize;
                yCenter *= modelSize;
                width *= modelSize;
                height *= modelSize;
            }

            if (!IsPlausibleModelBox(xCenter, yCenter, width, height, modelSize)) continue;

            float halfW = width * 0.5f;
            float halfH = height * 0.5f;

            float boxX = (xCenter - halfW) * scaleX + offsetX;
            float boxY = (yCenter - halfH) * scaleY + offsetY;
            float boxW = width * scaleX;
            float boxH = height * scaleY;

            if (boxW <= 0.0f || boxH <= 0.0f) continue;

            BoundingBox box;
            box.x = boxX;
            box.y = boxY;
            box.width = boxW;
            box.height = boxH;
            box.confidence = maxClassProb;
            box.classId = bestClassId;

            out.push(box);
        }
    }
}

void YoloDetector::postprocess(const ncnn::Mat& output, DetectionResult& result, int cropSize, bool fullFrame) {
    result.boxes.clear(); // Fix ghosting: Clear previous frame detections
    float confThreshold = confidenceThreshold_.load(std::memory_order_relaxed);
    
    int numBoxes, numValues;
    bool transposed = false;

    // Robust orientation detection (see decodeBoxes): lock onto the 5/6-channel
    // dimension instead of the fragile "smaller dimension wins" swap.
    if (output.w == 5 || output.w == 6) {
        numBoxes = output.h;
        numValues = output.w;
        transposed = false;
    } else if (output.h == 5 || output.h == 6) {
        numBoxes = output.w;
        numValues = output.h;
        transposed = true;
    } else {
        numBoxes = output.h;
        numValues = output.w;
        if (numBoxes < numValues) {
            transposed = true;
            std::swap(numBoxes, numValues);
        }
    }

    // Cache coordinate mapping scalars (Math Optimization)
    float captureWidth = static_cast<float>(currentCaptureWidth_ > 0 ? currentCaptureWidth_ : Config::CAPTURE_WIDTH);
    float captureHeight = static_cast<float>(currentCaptureHeight_ > 0 ? currentCaptureHeight_ : Config::CAPTURE_HEIGHT);
    float screenW = static_cast<float>(screenWidth_.load(std::memory_order_relaxed));
    float screenH = static_cast<float>(screenHeight_.load(std::memory_order_relaxed));
    
    float modelSize = static_cast<float>(Config::MODEL_INPUT_SIZE);

    // Combined scalars for single-fused multiply (Caching)
    float scaleX, scaleY, offsetX, offsetY;
    if (fullFrame) {
        // Whole-screen mode: model input maps linearly to the full screen.
        scaleX = screenW / modelSize;
        scaleY = screenH / modelSize;
        offsetX = 0.0f;
        offsetY = 0.0f;
    } else {
        float modelToCrop = static_cast<float>(cropSize) / modelSize;
        float captureToScreenX = screenW / captureWidth;
        float captureToScreenY = screenH / captureHeight;
        scaleX = modelToCrop * captureToScreenX;
        scaleY = modelToCrop * captureToScreenY;
        offsetX = static_cast<float>(currentCropX_) * captureToScreenX;
        offsetY = static_cast<float>(currentCropY_) * captureToScreenY;
    }
    
    int numClasses = numValues - 4;
    int classOffset = 4;
    float objectness = 1.0f;
    
    // Auto-detect YOLO version
    if (numClasses == Config::NUM_CLASSES + 1) {
        classOffset = 5;
        numClasses -= 1; 
    }
    if (numClasses < 1) numClasses = 1;

    // Optimized path: Split loops for Transposed vs Non-Transposed to enable SIMD
    if (transposed) {
        const float* row0 = output.row(0);
        const float* row1 = output.row(1);
        const float* row2 = output.row(2);
        const float* row3 = output.row(3);
        const float* rowObj = (classOffset > 4) ? output.row(4) : nullptr;
        
        for (int i = 0; i < numBoxes; ++i) {
            if (result.boxes.full()) break;

            float maxClassProb = 0.0f;
            int bestClassId = 0;
            
            if (classOffset > 4) objectness = rowObj[i];
            
            // Unrolling class loop slightly helpful, but dynamic count prevents full unroll
            for (int c = 0; c < numClasses; ++c) {
                float prob = output.row(classOffset + c)[i];
                prob *= objectness;
                if (prob > maxClassProb) {
                    maxClassProb = prob;
                    bestClassId = c;
                }
            }
            
            if (maxClassProb < confThreshold) continue;
            if (Config::FILTER_ENEMY_ONLY && bestClassId != Config::ENEMY_CLASS_ID) continue;

            float xCenter = row0[i];
            float yCenter = row1[i];
            float width = row2[i];
            float height = row3[i];

            // Normalize if needed (heuristic based on value range)
            if (xCenter <= 1.5f) {
                xCenter *= modelSize;
                yCenter *= modelSize;
                width *= modelSize;
                height *= modelSize;
            }

            if (!std::isfinite(maxClassProb)) continue;
            if (!IsPlausibleModelBox(xCenter, yCenter, width, height, modelSize)) continue;

            // Optimized coordinate transform (Fused Multiply-Add)
            // box.x = (xCenter - width/2) * scaleX + offsetX
            float halfW = width * 0.5f;
            float halfH = height * 0.5f;
            
            float boxX = (xCenter - halfW) * scaleX + offsetX;
            float boxY = (yCenter - halfH) * scaleY + offsetY;
            float boxW = width * scaleX;
            float boxH = height * scaleY;

            if (boxW <= 0.0f || boxH <= 0.0f) continue;
            
            BoundingBox box;
            box.x = boxX;
            box.y = boxY;
            box.width = boxW;
            box.height = boxH;
            box.confidence = maxClassProb;
            box.classId = bestClassId;
            
            result.boxes.push(box);
        }
    } else {
        // Non-transposed path (standard NCNN)
        for (int i = 0; i < numBoxes; ++i) {
            if (result.boxes.full()) break;

            const float* values = output.row(i);
            
            float maxClassProb = 0.0f;
            int bestClassId = 0;

            if (classOffset > 4) objectness = values[4];
            
            for (int c = 0; c < numClasses; ++c) {
                float prob = values[classOffset + c];
                prob *= objectness;
                if (prob > maxClassProb) {
                    maxClassProb = prob;
                    bestClassId = c;
                }
            }
            
            if (!std::isfinite(maxClassProb)) continue;
            if (maxClassProb < confThreshold) continue;
            if (Config::FILTER_ENEMY_ONLY && bestClassId != Config::ENEMY_CLASS_ID) continue;

            float xCenter = values[0];
            float yCenter = values[1];
            float width = values[2];
            float height = values[3];

            if (xCenter <= 1.5f) {
                xCenter *= modelSize;
                yCenter *= modelSize;
                width *= modelSize;
                height *= modelSize;
            }

            if (!IsPlausibleModelBox(xCenter, yCenter, width, height, modelSize)) continue;

            float halfW = width * 0.5f;
            float halfH = height * 0.5f;
            
            float boxX = (xCenter - halfW) * scaleX + offsetX;
            float boxY = (yCenter - halfH) * scaleY + offsetY;
            float boxW = width * scaleX;
            float boxH = height * scaleY;

            if (boxW <= 0.0f || boxH <= 0.0f) continue;

            BoundingBox box;
            box.x = boxX;
            box.y = boxY;
            box.width = boxW;
            box.height = boxH;
            box.confidence = maxClassProb;
            box.classId = bestClassId;
            
            result.boxes.push(box);
        }
    }
    
    if (result.boxes.size() > 1) {
        applyNMS(result.boxes);
    }
}

void YoloDetector::applyNMS(DetectionArray& boxes) {
    int count = boxes.size();
    if (count <= 1) return;
    
    boxes.sort([](const BoundingBox& a, const BoundingBox& b) {
        return a.confidence > b.confidence;
    });
    
    std::array<bool, Config::MAX_DETECTIONS> suppressed{};
    int finalCount = 0;
    
    for (int i = 0; i < count; ++i) {
        if (suppressed[i]) continue;
        
        if (i != finalCount) {
             boxes[finalCount] = boxes[i];
        }
        finalCount++;
        
        for (int j = i + 1; j < count; ++j) {
            if (!suppressed[j]) {
                float iou = boxes[i].iou(boxes[j]);
                if (iou > Config::NMS_IOU_THRESHOLD) {
                    suppressed[j] = true;
                }
            }
        }
    }
    
    // Compact FixedArray by recreating it (stack copy, zero allocation)
    DetectionArray newBoxes;
    for (int i = 0; i < finalCount; ++i) {
        newBoxes.push(boxes[i]);
    }
    // Assign back (copy data)
    boxes = newBoxes;
}

} // namespace ESP