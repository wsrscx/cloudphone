package com.cloudphone.client;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;

import java.util.ArrayList;
import java.util.List;

final class DeviceAdapter extends RecyclerView.Adapter<DeviceAdapter.Holder> {
    interface Listener {
        void onDeviceClicked(DeviceInfo device);
    }

    private final Listener listener;
    private final List<DeviceInfo> devices = new ArrayList<>();
    private boolean enabled = true;

    DeviceAdapter(Listener listener) {
        this.listener = listener;
    }

    void submit(List<DeviceInfo> values) {
        devices.clear();
        devices.addAll(values);
        notifyDataSetChanged();
    }

    void setEnabled(boolean value) {
        enabled = value;
        notifyDataSetChanged();
    }

    @NonNull @Override public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        Context context = parent.getContext();
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(context, 18), dp(context, 14), dp(context, 18), dp(context, 14));
        GradientDrawable background = new GradientDrawable();
        background.setColor(context.getColor(R.color.cloud_surface));
        background.setCornerRadius(dp(context, 8));
        row.setBackground(background);

        TextView name = text(context, 18, R.color.cloud_text);
        name.setTypeface(Typeface.DEFAULT_BOLD);
        TextView details = text(context, 14, R.color.cloud_muted);
        TextView state = text(context, 14, R.color.cloud_online);
        row.addView(name, matchWrap());
        row.addView(details, matchWrap());
        row.addView(state, matchWrap());
        RecyclerView.LayoutParams params = new RecyclerView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 0, 0, dp(context, 10));
        row.setLayoutParams(params);
        return new Holder(row, name, details, state);
    }

    @Override public void onBindViewHolder(@NonNull Holder holder, int position) {
        DeviceInfo device = devices.get(position);
        holder.name.setText(device.name);
        String details = device.model + (device.androidVersion.isEmpty() ? "" : " · Android " + device.androidVersion);
        holder.details.setText(details);
        if (!device.isConnectable()) {
            holder.state.setText(R.string.offline);
            holder.state.setTextColor(holder.itemView.getContext().getColor(R.color.cloud_muted));
        } else {
            holder.state.setText(R.string.online);
            holder.state.setTextColor(holder.itemView.getContext().getColor(R.color.cloud_online));
        }
        holder.itemView.setAlpha(enabled && device.isConnectable() ? 1f : 0.65f);
        holder.itemView.setOnClickListener(view -> {
            if (enabled && device.isConnectable()) {
                listener.onDeviceClicked(device);
            }
        });
    }

    @Override public int getItemCount() {
        return devices.size();
    }

    private static TextView text(Context context, int sizeSp, int color) {
        TextView view = new TextView(context);
        view.setTextSize(sizeSp);
        view.setTextColor(context.getColor(color));
        view.setPadding(0, dp(context, 2), 0, dp(context, 2));
        return view;
    }

    private static LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private static int dp(Context context, int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    static final class Holder extends RecyclerView.ViewHolder {
        final TextView name;
        final TextView details;
        final TextView state;

        Holder(@NonNull View itemView, TextView name, TextView details, TextView state) {
            super(itemView);
            this.name = name;
            this.details = details;
            this.state = state;
        }
    }
}
