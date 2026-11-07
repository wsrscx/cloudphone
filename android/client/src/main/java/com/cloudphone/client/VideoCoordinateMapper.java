package com.cloudphone.client;

/** Maps view coordinates through aspect-fit letterboxing into the protocol's unsigned 16-bit space. */
public final class VideoCoordinateMapper {
    public static final int NORMALIZED_MAX = 65_535;

    private int viewWidth;
    private int viewHeight;
    private int videoWidth;
    private int videoHeight;

    public synchronized void updateViewSize(int width, int height) {
        viewWidth = Math.max(0, width);
        viewHeight = Math.max(0, height);
    }

    public synchronized void updateVideoSize(int width, int height) {
        videoWidth = Math.max(0, width);
        videoHeight = Math.max(0, height);
    }

    public synchronized MappedPoint map(float x, float y, boolean clampToVideo) {
        if (viewWidth == 0 || viewHeight == 0 || videoWidth == 0 || videoHeight == 0) {
            return null;
        }
        double scale = Math.min((double) viewWidth / videoWidth, (double) viewHeight / videoHeight);
        double renderedWidth = videoWidth * scale;
        double renderedHeight = videoHeight * scale;
        double left = (viewWidth - renderedWidth) / 2.0d;
        double top = (viewHeight - renderedHeight) / 2.0d;
        double right = left + renderedWidth;
        double bottom = top + renderedHeight;
        if (!clampToVideo && (x < left || x > right || y < top || y > bottom)) {
            return null;
        }
        double boundedX = Math.max(left, Math.min(right, x));
        double boundedY = Math.max(top, Math.min(bottom, y));
        int normalizedX = normalize((boundedX - left) / renderedWidth);
        int normalizedY = normalize((boundedY - top) / renderedHeight);
        return new MappedPoint(normalizedX, normalizedY);
    }

    private static int normalize(double fraction) {
        return (int) Math.round(Math.max(0d, Math.min(1d, fraction)) * NORMALIZED_MAX);
    }

    public static final class MappedPoint {
        public final int x;
        public final int y;

        MappedPoint(int x, int y) {
            this.x = x;
            this.y = y;
        }
    }
}

