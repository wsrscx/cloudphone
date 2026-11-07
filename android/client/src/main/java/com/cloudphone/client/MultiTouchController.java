package com.cloudphone.client;

import android.util.SparseArray;
import android.util.SparseBooleanArray;
import android.view.Choreographer;
import android.view.MotionEvent;
import android.view.View;

import com.cloudphone.protocol.CloudPhoneProtocol.TouchAction;

import java.util.ArrayList;
import java.util.List;

/** Converts Android pointer IDs into stable remote pointers and coalesces MOVE events once per frame. */
public final class MultiTouchController implements View.OnTouchListener {
    private static final int MAX_POINTERS = 10;

    private final ControlProtocol protocol;
    private final VideoCoordinateMapper mapper;
    private final SparseBooleanArray active = new SparseBooleanArray();
    private final SparseArray<ControlProtocol.Pointer> lastPointers = new SparseArray<>();
    private final SparseArray<ControlProtocol.Pointer> pendingMoves = new SparseArray<>();
    private boolean moveScheduled;
    private boolean enabled = true;

    private final Choreographer.FrameCallback moveFrame = frameTimeNanos -> flushMoves();

    public MultiTouchController(ControlProtocol protocol, VideoCoordinateMapper mapper) {
        this.protocol = protocol;
        this.mapper = mapper;
    }

    public void setEnabled(boolean value) {
        if (enabled && !value) cancelAll();
        enabled = value;
    }

    @Override public boolean onTouch(View view, MotionEvent event) {
        if (!enabled) return true;
        mapper.updateViewSize(view.getWidth(), view.getHeight());
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
            case MotionEvent.ACTION_POINTER_DOWN:
                pointerDown(event, event.getActionIndex());
                break;
            case MotionEvent.ACTION_MOVE:
                pointerMoves(event);
                break;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_POINTER_UP:
                pointerUp(event, event.getActionIndex());
                break;
            case MotionEvent.ACTION_CANCEL:
                cancelAll();
                break;
            default:
                break;
        }
        return true;
    }

    public void cancelAll() {
        if (moveScheduled) {
            Choreographer.getInstance().removeFrameCallback(moveFrame);
            moveScheduled = false;
        }
        pendingMoves.clear();
        protocol.clearPendingMoves();
        for (int i = 0; i < active.size(); i++) {
            int id = active.keyAt(i);
            if (!active.valueAt(i)) continue;
            ControlProtocol.Pointer pointer = lastPointers.get(id);
            if (pointer != null) protocol.sendTouch(TouchAction.TOUCH_CANCEL, pointer);
        }
        active.clear();
        lastPointers.clear();
    }

    int activePointerCount() {
        int count = 0;
        for (int i = 0; i < active.size(); i++) if (active.valueAt(i)) count++;
        return count;
    }

    private void pointerDown(MotionEvent event, int index) {
        if (activePointerCount() >= MAX_POINTERS) return;
        int id = event.getPointerId(index);
        VideoCoordinateMapper.MappedPoint point = mapper.map(event.getX(index), event.getY(index), false);
        if (point == null) return;
        ControlProtocol.Pointer pointer = pointer(id, point, event.getPressure(index));
        protocol.discardPendingMove(id);
        if (!protocol.sendTouch(TouchAction.TOUCH_DOWN, pointer)) return;
        active.put(id, true);
        lastPointers.put(id, pointer);
    }

    private void pointerMoves(MotionEvent event) {
        for (int index = 0; index < event.getPointerCount(); index++) {
            int id = event.getPointerId(index);
            if (!active.get(id)) continue;
            VideoCoordinateMapper.MappedPoint point = mapper.map(event.getX(index), event.getY(index), true);
            if (point == null) continue;
            ControlProtocol.Pointer pointer = pointer(id, point, event.getPressure(index));
            pendingMoves.put(id, pointer);
            lastPointers.put(id, pointer);
        }
        if (!moveScheduled && pendingMoves.size() > 0) {
            moveScheduled = true;
            Choreographer.getInstance().postFrameCallback(moveFrame);
        }
    }

    private void pointerUp(MotionEvent event, int index) {
        int id = event.getPointerId(index);
        if (!active.get(id)) return;
        VideoCoordinateMapper.MappedPoint point = mapper.map(event.getX(index), event.getY(index), true);
        ControlProtocol.Pointer pointer = point == null ? lastPointers.get(id)
                : pointer(id, point, event.getPressure(index));
        protocol.discardPendingMove(id);
        if (pointer != null) protocol.sendTouch(TouchAction.TOUCH_UP, pointer);
        active.delete(id);
        lastPointers.remove(id);
        pendingMoves.remove(id);
    }

    private void flushMoves() {
        moveScheduled = false;
        List<ControlProtocol.Pointer> batch = new ArrayList<>(pendingMoves.size());
        for (int i = 0; i < pendingMoves.size(); i++) batch.add(pendingMoves.valueAt(i));
        pendingMoves.clear();
        protocol.sendMoves(batch);
    }

    private static ControlProtocol.Pointer pointer(int id, VideoCoordinateMapper.MappedPoint point,
                                                   float pressure) {
        float finite = Float.isFinite(pressure) ? pressure : 0f;
        int normalizedPressure = Math.round(Math.max(0f, Math.min(1f, finite))
                * VideoCoordinateMapper.NORMALIZED_MAX);
        return new ControlProtocol.Pointer(id, point.x, point.y, normalizedPressure);
    }
}
