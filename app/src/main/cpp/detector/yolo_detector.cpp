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

    // ------------------------------------------------------------------
    // NCNN Warm-up: run a dummy inference to precompile Vulkan shaders.
    //
    // The FIRST real inference on a Vulkan-backed NCNN net takes 200-500ms
    // longer than subsequent ones because the driver must compile and cache
    // SPIR-V shaders for every layer. If this happens during the first captured
    // frame, the user sees a massive stutter and the temporal filter's track
    // table gets seeded with stale positions. Running a throwaway inference
    // here — with a zeroed input on the init thread — absorbs that cost so the
    // first real frame is already at steady-state speed.
    // ------------------------------------------------------------------
    {
        // Create a zeroed input Mat directly (don't use from_pixels with
        // nullptr — NCNN will dereference the pointer and crash).
        ncnn::Mat warmupInput;
        warmupInput.create(Config::MODEL_INPUT_SIZE, Config::MODEL_INPUT_SIZE, 3);
        if (!warmupInput.empty()) {
            // Fill with 114.0f to match letterbox padding value, then normalize.
            // The actual content doesn't matter — we just need to trigger
            // Vulkan shader compilation for every layer in the network.
            const int total = warmupInput.cstep * warmupInput.c;
            std::fill(static_cast<float*>(warmupInput.data),
                      static_cast<float*>(warmupInput.data) + total, 114.0f);

            // Normalize [0,255] → [0,1], same as real preprocessing.
            const float meanVals[3] = {0.0f, 0.0f, 0.0f};
            const float normVals[3] = {1.0f / 255.0f, 1.0f / 255.0f, 1.0f / 255.0f};
            warmupInput.substract_mean_normalize(meanVals, normVals);

            auto warmupStart = std::chrono::high_resolution_clock::now();
            ncnn::Mat warmupOutput;
            runInference(warmupInput, warmupOutput);
            auto warmupEnd = std::chrono::high_resolution_clock::now();
            float warmupMs = std::chrono::duration<float, std::milli>(warmupEnd - warmupStart).count();
            LOGI("NCNN warm-up inference: %.1f ms (shaders compiled & cached)", warmupMs);
        }
    }

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
    // Delegate to the 4-argument version with full-frame mode.
    // The old tiled detection path (6 inferences per frame) was removed because
    // it was 6x slower than single-pass and broke the temporal filter.
    return detect(buffer, result, Config::CROP_SIZE, true);
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
        currentActualCropSize_ = actualCropSize;

        // Center crop coordinates
        int cropX = (srcWidth - actualCropSize) / 2;
        int cropY = (srcHeight - actualCropSize) / 2;
        currentCropX_ = std::max(0, std::min(cropX, srcWidth - actualCropSize));
        currentCropY_ = std::max(0, std::min(cropY, srcHeight - actualCropSize));
        actualW = actualCropSize;
        actualH = actualCropSize;
    }

    // ------------------------------------------------------------------
    // Letterbox resize (matches YOLOv8/v26 training preprocessing).
    //
    // The model was trained with Ultralytics' default letterbox resize:
    // scale = min(modelW/srcW, modelH/srcH), resize, then pad to square with
    // value 114. The OLD inference code used from_pixels_resize() which does a
    // STRETCH resize (ignoring aspect ratio). For a 1280x720 capture → 256x256
    // model input, that's a 16:9→1:1 distortion the model never saw in training,
    // which silently degrades mAP by 10-20% and causes both missed detections
    // (distorted targets fall below threshold) and phantom detections (distorted
    // background noise rises above threshold).
    // ------------------------------------------------------------------
    const uint8_t* srcStart = srcPixels + currentCropY_ * srcStride + currentCropX_ * 4;

    const int modelSize = Config::MODEL_INPUT_SIZE;
    const float scale = std::min(
        static_cast<float>(modelSize) / static_cast<float>(actualW),
        static_cast<float>(modelSize) / static_cast<float>(actualH)
    );
    const int resizedW = std::max(1, static_cast<int>(actualW * scale));
    const int resizedH = std::max(1, static_cast<int>(actualH * scale));
    const int padX = (modelSize - resizedW) / 2;
    const int padY = (modelSize - resizedH) / 2;

    // Store for postprocess coordinate mapping.
    letterboxResizedW_ = resizedW;
    letterboxResizedH_ = resizedH;
    letterboxPadX_ = padX;
    letterboxPadY_ = padY;

    // Step 1: Resize source pixels to the letterbox content size (RGBA→RGB).
    //         Buffer is still locked — from_pixels_resize reads from srcStart.
    ncnn::Mat resized = ncnn::Mat::from_pixels_resize(
        srcStart,
        ncnn::Mat::PIXEL_RGBA2RGB,
        actualW, actualH,
        srcStride,
        resizedW, resizedH
    );

    // Buffer no longer needed.
    int unlockResult = AHardwareBuffer_unlock(buffer, nullptr);
    if (unlockResult != 0) {
        LOGW("AHardwareBuffer_unlock failed: %d", unlockResult);
    }

    if (resized.empty()) {
        LOGE("Letterbox resize failed: from_pixels_resize returned empty Mat");
        return false;
    }

    // Step 2: Pad to square (letterbox). Value 114.0f is the standard YOLO
    //         padding value; after /255 normalisation it becomes ~0.447.
    //         copy_make_border handles zero-pad correctly (just copies).
    const int padRight = modelSize - resizedW - padX;
    const int padBottom = modelSize - resizedH - padY;
    ncnn::copy_make_border(resized, inputMat,
        padY, padBottom, padX, padRight,
        0,  // BORDER_CONSTANT
        114.0f,
        ncnn::Option()
    );

    if (inputMat.empty()) {
        LOGE("Letterbox padding failed: copy_make_border returned empty Mat");
        return false;
    }

    // Step 3: Normalize [0,255] → [0,1].
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
///
/// TIGHTENED from original:
///   - Min size 2→4 px: boxes smaller than 4px in model space (256px) are
///     sub-pixel noise, not real targets.
///   - Max size 1.05→0.98 * modelSize: a box larger than the input image
///     itself is physically impossible for a real target.
///   - Aspect ratio 8.0/0.125 → 5.0/0.2: human figures in a FPS are roughly
///     0.3-3.0 aspect ratio. 5.0/0.2 gives margin for partial-body detections
///     while still rejecting the extreme slivers that signal phantom noise.
///   - Margin 0.15→0.08: detections whose center is more than 8% outside the
///     model input are reading from padding, not real content.
inline bool IsPlausibleModelBox(float cx, float cy, float w, float h, float modelSize) {
    if (!std::isfinite(cx) || !std::isfinite(cy) ||
        !std::isfinite(w) || !std::isfinite(h)) {
        return false;
    }
    if (w < 4.0f || h < 4.0f) return false;
    if (w > modelSize * 0.98f || h > modelSize * 0.98f) return false;

    const float margin = modelSize * 0.08f;
    if (cx < -margin || cx > modelSize + margin) return false;
    if (cy < -margin || cy > modelSize + margin) return false;

    const float ratio = w / h;
    if (ratio > 5.0f || ratio < 0.2f) return false;

    return true;
}

} // namespace

// decodeBoxes() removed: this was dead code that duplicated the transposed/
// non-transposed decoding logic already inlined in postprocess(). Keeping a
// separate decode function risked divergence between the two paths and added
// maintenance burden without any call site.

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
        // Letterbox-aware coordinate mapping.
        //
        // Model input (256x256) contains the resized content at offset
        // (letterboxPadX_, letterboxPadY_) with size (letterboxResizedW_ x
        // letterboxResizedH_). To map a model-space coordinate back to screen
        // space we must:
        //   1. Subtract the letterbox padding offset.
        //   2. Scale from content-space to screen-space.
        //
        // The combined transform is:
        //   screenX = (modelX - padX) * (screenW / resizedW)
        //   screenY = (modelY - padY) * (screenH / resizedH)
        //
        // Which expands to:
        //   scaleX  = screenW / resizedW
        //   offsetX = -padX * scaleX
        // (and similarly for Y).
        const float rW = static_cast<float>(std::max(1, letterboxResizedW_));
        const float rH = static_cast<float>(std::max(1, letterboxResizedH_));
        scaleX  = screenW / rW;
        scaleY  = screenH / rH;
        offsetX = -static_cast<float>(letterboxPadX_) * scaleX;
        offsetY = -static_cast<float>(letterboxPadY_) * scaleY;
    } else {
        float modelToCrop = static_cast<float>(std::max(32, currentActualCropSize_)) / modelSize;
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

    // ------------------------------------------------------------------
    // Weighted Box Fusion (WBF)
    //
    // Replaces hard NMS which simply keeps the highest-confidence box and
    // discards all overlapping ones. WBF instead FUSES overlapping boxes
    // into a single box whose position and size are the confidence-weighted
    // average of every box in the cluster.
    //
    // Benefits over hard NMS:
    //   - More accurate box positions: averaging multiple slightly-offset
    //     detections cancels out per-frame jitter → tighter, more stable boxes.
    //   - Higher effective recall: a target detected at 0.48 confidence by
    //     two nearby anchors would be suppressed by NMS (only 0.48 survives),
    //     but WBF fuses them → the fused box gets boosted confidence, making
    //     it more likely to pass the temporal confirmation filter.
    //   - Smoother temporal tracking: because the fused box position is an
    //     average, it moves less erratically between frames, which keeps the
    //     IoU gate in the temporal filter happy.
    // ------------------------------------------------------------------
    boxes.sort([](const BoundingBox& a, const BoundingBox& b) {
        return a.confidence > b.confidence;
    });

    std::array<bool, Config::MAX_DETECTIONS> used{};
    DetectionArray fused;

    for (int i = 0; i < count; ++i) {
        if (used[i]) continue;

        // Start a new cluster with box i (highest unused confidence).
        used[i] = true;
        float sumWeight = boxes[i].confidence;
        float sumX  = boxes[i].x * boxes[i].confidence;
        float sumY  = boxes[i].y * boxes[i].confidence;
        float sumW  = boxes[i].width * boxes[i].confidence;
        float sumH  = boxes[i].height * boxes[i].confidence;
        float sumConf = boxes[i].confidence;
        int clusterSize = 1;

        // Find all remaining boxes that overlap with the cluster seed.
        for (int j = i + 1; j < count; ++j) {
            if (used[j]) continue;
            float iou = boxes[i].iou(boxes[j]);
            if (iou > Config::NMS_IOU_THRESHOLD) {
                used[j] = true;
                const float w = boxes[j].confidence;
                sumWeight += w;
                sumX += boxes[j].x * w;
                sumY += boxes[j].y * w;
                sumW += boxes[j].width * w;
                sumH += boxes[j].height * w;
                sumConf += boxes[j].confidence;
                clusterSize++;
            }
        }

        if (fused.full()) break;

        // Create the fused box.
        BoundingBox fb;
        if (sumWeight > 0.0f) {
            fb.x = sumX / sumWeight;
            fb.y = sumY / sumWeight;
            fb.width = sumW / sumWeight;
            fb.height = sumH / sumWeight;
        } else {
            fb = boxes[i];
        }
        // Fused confidence: average of cluster confidences, slightly boosted
        // by cluster size to reward agreement between multiple anchors.
        // boost = 1 + 0.03*(clusterSize-1), capped at 1.15.
        const float boost = std::min(1.15f, 1.0f + 0.03f * (clusterSize - 1));
        fb.confidence = std::min(1.0f, (sumConf / clusterSize) * boost);
        fb.classId = boxes[i].classId;

        fused.push(fb);
    }

    boxes = fused;
}

} // namespace ESP