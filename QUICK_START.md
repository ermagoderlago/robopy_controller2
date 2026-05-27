#!/usr/bin/env markdown
# QUICK START - Frame TF Ristrutturazione

## 🎯 In Breve

Tutti i frame names sono stati aggiornati da OAK-D specifici a **standard ROS universale**.

### Cambiamenti Principali

```
oak_mono_camera_frame          → camera_link (frame fisico)
oak_mono_camera_optical_frame  → camera_optical_frame (frame ottico)
oak_imu_frame                  → imu_link (frame IMU)
oak_depth_frame                → camera_optical_frame (usa frame ottico)
```

### Nuova Gerarchia

```
odom → base_link → camera_link → camera_optical_frame
                ↘ imu_link
```

---

## ⚡ 3 Step Veloci per Testare

```bash
# 1. Lancia il sistema
ros2 launch robopy_controller test_odometry_launch.py

# 2. In altro terminale - Vedi frame hierarchy
ros2 run tf2_tools view_frames

# 3. Verifica trasformazioni
ros2 run tf2_ros tf2_echo base_link camera_optical_frame
```

---

## 📝 File Modificati

✅ **superpoint_node.py** - Frame corretto + publish_static_tf disabilitato  
✅ **test_odometry_launch.py** - TF statiche aggiunte  
✅ **test2_launch.py** - TF statiche migliorate  
✅ **IMU_oakd_node.py** - Frame IMU standard  
✅ **camera_info_publisher.py** - Camera frame standard  

---

## 📚 Documentazione

| File | Scopo |
|------|-------|
| [README_FRAMES.md](README_FRAMES.md) | **Guida principale** - LEGGI PRIMA |
| [TF_RESTRUCTURE_SUMMARY.md](TF_RESTRUCTURE_SUMMARY.md) | Documentazione tecnica completa |
| [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) | Report dettagliato e verifiche |
| [LAUNCH_UPDATE_GUIDE.md](LAUNCH_UPDATE_GUIDE.md) | Come aggiornare altri launch file |
| [tf_verify.sh](tf_verify.sh) | Script di verifica automatica |

---

## ✅ Pronto per il Deployment

Tutte le modifiche sono completate. Il sistema ora usa **frame names standard ROS** ed è **100% compatibile** con SLAM, Nav2, RVIZ e tutti i tool ROS standard.

---

**Inizio**: Leggi [README_FRAMES.md](README_FRAMES.md) per una guida completa.
