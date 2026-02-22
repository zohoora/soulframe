#version 330 core

in vec2 v_texcoord;
out vec4 fragColor;

// Textures
uniform sampler2D u_texture;
uniform sampler2D u_texture_prev;

// Global
uniform float u_time;
uniform vec2 u_gaze_pos;
uniform float u_crossfade;          // 0 = fully previous, 1 = fully current

// Breathing params
uniform float u_breath_amplitude;   // ~0.008
uniform float u_breath_frequency;   // ~0.25 Hz
uniform float u_breath_intensity;   // 0-1
uniform vec2 u_breath_center;       // region center
uniform float u_breath_radius;      // region radius

// Parallax params
uniform float u_parallax_intensity; // 0-1
uniform float u_parallax_depth_scale; // ~0.01

// Ken Burns params
uniform float u_kb_intensity;       // 0-1
uniform float u_kb_zoom_speed;      // ~0.001
uniform vec2 u_kb_pan_dir;          // normalized pan direction

// Vignette params
uniform float u_vignette_intensity; // 0-1
uniform float u_vignette_softness;  // ~0.45
uniform float u_vignette_radius;    // ~0.75


// Apply Ken Burns: slow zoom + pan
vec2 apply_kenburns(vec2 uv) {
    float zoom = 1.0 + u_time * u_kb_zoom_speed * u_kb_intensity;
    vec2 center = vec2(0.5, 0.5);
    uv = center + (uv - center) / zoom;
    vec2 pan_offset = u_kb_pan_dir * u_time * u_kb_zoom_speed * 0.5 * u_kb_intensity;
    uv += pan_offset;
    return uv;
}

// Apply parallax depth shift based on gaze
vec2 apply_parallax(vec2 uv) {
    vec2 gaze_offset = uv - u_gaze_pos;
    vec2 shift = gaze_offset * u_parallax_depth_scale * u_parallax_intensity;
    uv -= shift;
    return uv;
}

// Apply breathing distortion around a region
vec2 apply_breathing(vec2 uv) {
    vec2 delta = uv - u_breath_center;
    float dist = length(delta);
    float falloff = 1.0 - smoothstep(u_breath_radius * 0.6, u_breath_radius, dist);
    float breath = sin(u_time * u_breath_frequency * 6.2831853) * u_breath_amplitude;
    float strength = breath * falloff * u_breath_intensity;
    uv = u_breath_center + delta * (1.0 + strength);
    return uv;
}

// Compute vignette darkening factor
float compute_vignette() {
    vec2 center_offset = v_texcoord - vec2(0.5, 0.5);
    float dist = length(center_offset);
    float vignette = 1.0 - smoothstep(u_vignette_radius, u_vignette_radius + u_vignette_softness, dist);
    return mix(1.0, vignette, u_vignette_intensity);
}


void main() {
    vec2 uv = v_texcoord;

    // 1. Ken Burns (slow zoom + pan)
    uv = apply_kenburns(uv);

    // 2. Parallax (gaze-based depth shift)
    uv = apply_parallax(uv);

    // 3. Breathing (subtle pulsing around region)
    uv = apply_breathing(uv);

    // 4. Sample the current texture with distorted UVs
    vec4 color_current = texture(u_texture, uv);

    // 5. Vignette darkening
    float vignette_factor = compute_vignette();
    color_current.rgb *= vignette_factor;

    // 6. Crossfade with previous texture
    // For the previous texture, apply the same UV distortions for consistency
    vec4 color_prev = texture(u_texture_prev, uv);
    color_prev.rgb *= vignette_factor;

    fragColor = mix(color_prev, color_current, u_crossfade);
}
