package com.cloudphone.client;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class AspectFitLayoutTest {
    @Test public void fitsLandscapeVideoAboveRightToolbarWithoutCropping() {
        AspectFitLayout.Bounds bounds = AspectFitLayout.fit(1920, 1130, 1920, 1080);
        assertEquals(0, bounds.left);
        assertEquals(25, bounds.top);
        assertEquals(1920, bounds.width);
        assertEquals(1080, bounds.height);
    }

    @Test public void centersPortraitVideoAboveBottomToolbar() {
        AspectFitLayout.Bounds bounds = AspectFitLayout.fit(1080, 1844, 1080, 1920);
        assertEquals(21, bounds.left);
        assertEquals(0, bounds.top);
        assertEquals(1037, bounds.width);
        assertEquals(1844, bounds.height);
    }

    @Test public void keepsNativePixelsWhenViewportIsOnlySlightlyLarger() {
        AspectFitLayout.Bounds bounds = AspectFitLayout.fit(1928, 1130, 1920, 1080);
        assertEquals(4, bounds.left);
        assertEquals(25, bounds.top);
        assertEquals(1920, bounds.width);
        assertEquals(1080, bounds.height);
    }

    @Test public void stillUpscalesForMuchLargerDisplays() {
        AspectFitLayout.Bounds bounds = AspectFitLayout.fit(3840, 2160, 1920, 1080);
        assertEquals(3840, bounds.width);
        assertEquals(2160, bounds.height);
    }

    @Test public void invalidGeometryProducesEmptyBounds() {
        AspectFitLayout.Bounds bounds = AspectFitLayout.fit(0, 100, 16, 9);
        assertEquals(0, bounds.width);
        assertEquals(0, bounds.height);
    }
}
