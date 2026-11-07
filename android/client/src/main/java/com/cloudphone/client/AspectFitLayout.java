package com.cloudphone.client;

/** Computes a centered, uncropped video surface inside the controls viewport. */
public final class AspectFitLayout {
    private static final double MAX_NEAR_NATIVE_UPSCALE = 1.15d;

    private AspectFitLayout() {}

    public static Bounds fit(int containerWidth, int containerHeight, int videoWidth, int videoHeight) {
        if (containerWidth <= 0 || containerHeight <= 0 || videoWidth <= 0 || videoHeight <= 0) {
            return new Bounds(0, 0, 0, 0);
        }
        double scale = Math.min(
                (double) containerWidth / videoWidth,
                (double) containerHeight / videoHeight);
        // Small non-integer upscales soften text and game textures. If the
        // native frame already nearly fills the viewport, keep exact pixels.
        if (scale > 1d && scale <= MAX_NEAR_NATIVE_UPSCALE) scale = 1d;
        int width = Math.min(containerWidth, Math.max(1, (int) Math.round(videoWidth * scale)));
        int height = Math.min(containerHeight, Math.max(1, (int) Math.round(videoHeight * scale)));
        return new Bounds((containerWidth - width) / 2, (containerHeight - height) / 2, width, height);
    }

    public static final class Bounds {
        public final int left;
        public final int top;
        public final int width;
        public final int height;

        Bounds(int left, int top, int width, int height) {
            this.left = left;
            this.top = top;
            this.width = width;
            this.height = height;
        }
    }
}
