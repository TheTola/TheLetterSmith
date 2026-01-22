
# visualizer_demo.py — Demo wired to AudioVisualizerUltra in audio_visualizer.py
from __future__ import annotations

import sys
from pathlib import Path
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QFileDialog

from audio_visualizer import AudioVisualizerUltra

class Demo(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ultra Visualizer — Demo")
        self.resize(1100, 620)

        self.player = QMediaPlayer(self)
        self.out = QAudioOutput(self); self.player.setAudioOutput(self.out); self.out.setVolume(0.9)

        self.vis = AudioVisualizerUltra(self)
        self.vis.set_media_player(self.player)

        open_btn = QtWidgets.QPushButton("Open…")
        play_btn = QtWidgets.QPushButton("Play")
        pause_btn = QtWidgets.QPushButton("Pause")
        stop_btn = QtWidgets.QPushButton("Stop")

        wave = QtWidgets.QCheckBox("Waveform"); wave.toggled.connect(self.vis.set_show_waveform)
        flux = QtWidgets.QCheckBox("Percussion Flash"); flux.setChecked(True); flux.toggled.connect(self.vis.set_enable_flux_flash)
        subg = QtWidgets.QCheckBox("Sub Glow"); subg.setChecked(True); subg.toggled.connect(self.vis.set_enable_sub_glow)
        sprk = QtWidgets.QCheckBox("Sparkles"); sprk.setChecked(True); sprk.toggled.connect(self.vis.set_enable_sparkles)
        scan = QtWidgets.QCheckBox("Scanline"); scan.setChecked(True); scan.toggled.connect(self.vis.set_enable_scanline)

        flash_slider = QtWidgets.QSlider(Qt.Horizontal); flash_slider.setMinimum(0); flash_slider.setMaximum(200); flash_slider.setValue(100)
        flash_slider.valueChanged.connect(lambda v: self.vis.set_flashiness(v/100.0))

        bars_slider = QtWidgets.QSlider(Qt.Horizontal); bars_slider.setMinimum(4); bars_slider.setMaximum(28); bars_slider.setValue(14)
        bars_slider.valueChanged.connect(self.vis.set_bars_per_group)

        ctrl = QtWidgets.QGridLayout()
        ctrl.addWidget(open_btn, 0, 0, 1, 1)
        ctrl.addWidget(QtWidgets.QLabel("Flashiness"), 0, 1)
        ctrl.addWidget(flash_slider, 0, 2, 1, 2)
        ctrl.addWidget(QtWidgets.QLabel("Bars/Group"), 0, 4)
        ctrl.addWidget(bars_slider, 0, 5)
        ctrl.addWidget(wave, 1, 1); ctrl.addWidget(flux, 1, 2); ctrl.addWidget(subg, 1, 3); ctrl.addWidget(sprk, 1, 4); ctrl.addWidget(scan, 1, 5)
        ctrl.addWidget(play_btn, 0, 6); ctrl.addWidget(pause_btn, 0, 7); ctrl.addWidget(stop_btn, 0, 8)
        ctrl.setColumnStretch(3, 1)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.vis, 1)
        lay.addLayout(ctrl)

        open_btn.clicked.connect(self.open_file)
        play_btn.clicked.connect(self.player.play)
        pause_btn.clicked.connect(self.player.pause)
        stop_btn.clicked.connect(self.player.stop)

    @QtCore.Slot()
    def open_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Choose audio", str(Path.home()),
                                            "Audio (*.mp3 *.wav *.m4a *.aac *.flac);;All files (*.*)")
        if not fn: return
        self.player.setSource(QtCore.QUrl.fromLocalFile(fn))
        self.vis.set_audio_file(fn)
        self.player.play()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = Demo(); w.show()
    sys.exit(app.exec())
