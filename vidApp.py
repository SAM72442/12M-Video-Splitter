import sys
import cv2
import ffmpeg
import os
import tempfile
import subprocess
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QLabel, QFileDialog, QVBoxLayout, QSlider, QHBoxLayout, QProgressBar, QMessageBox, QStackedLayout, QSizePolicy, QSpacerItem, QDialog, QLineEdit
from PyQt6.QtGui import QPixmap, QIcon, QPainter, QColor
from PyQt6.QtCore import Qt, QTimer, QUrl, QPropertyAnimation, QThread, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

BASE_PATH = os.path.dirname(os.path.abspath(__file__))

class VideoEditorApp(QWidget):
    def __init__(self):
        super().__init__()
        
        self.mediaPlayer = QMediaPlayer()
        self.audioOutput = QAudioOutput()
        self.mediaPlayer.setAudioOutput(self.audioOutput)
        self.audioOutput.setVolume(1.0)
        
        self.video_path = None
        self.original_video_path = None
        self.cap = None
        self.frame_count = 0
        self.fps = 30
        self.split_points = []
        self.deactivated_segments = []
        self.undo_stack = []
        self.redo_stack = []
        self.paused = False
        
        self.full_ui_setup = False
        self.loading_label = QLabel("Loading", self)
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.hide()
        self.loading_timer = QTimer(self)
        self.loading_timer.timeout.connect(self.update_loading_text)
        self.loading_state = 0
        
        # Download animation setup
        self.download_timer = QTimer(self)
        self.download_timer.timeout.connect(self.update_download_text)
        self.download_state = 0
        self.active_download_button = None  # Track which button is animating
        
        self.initUI()

    def initUI(self):
        self.setWindowTitle("12MVideoSplitter")
        self.setGeometry(100, 100, 200, 200)  # Small square window
        self.setWindowIcon(QIcon(os.path.join(BASE_PATH, "resources/images/logo.ico")))
        
        self.openButton = QPushButton("Open Video", self)
        self.openButton.setStyleSheet("border-radius: 50px; background-color: #4CAF50; color: white;")
        self.openButton.setFixedSize(100, 100)
        self.openButton.clicked.connect(self.openFile)
        
        # Ensure loading label matches button size for consistent centering
        self.loading_label.setFixedSize(100, 100)
        
        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(self.openButton, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.loading_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        self.setLayout(layout)

    def setupFullUI(self):
        if self.full_ui_setup:
            return
        
        # Clear the existing layout completely
        if self.layout() is not None:
            while self.layout().count():
                item = self.layout().takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            QWidget().setLayout(self.layout())
        
        # Video container with flexible layout
        self.videoContainer = QWidget(self)
        videoLayout = QVBoxLayout(self.videoContainer)
        videoLayout.setContentsMargins(0, 0, 0, 0)
        self.videoWidget = QVideoWidget(self.videoContainer)
        self.videoWidget.setStyleSheet("background: transparent;")
        videoLayout.addWidget(self.videoWidget)
        
        self.mediaPlayer.setVideoOutput(self.videoWidget)
        self.videoWidget.show()
        self.videoContainer.show()
        
        self.videoContainer.setMouseTracking(True)
        self.videoContainer.mousePressEvent = self.togglePlayPauseOnClick
        
        self.addSplitButton = QPushButton("Add Split Point", self)
        self.addSplitButton.clicked.connect(self.addSplitPoint)
        
        self.deactivateButton = QPushButton("Deactivate Segment", self)
        self.deactivateButton.clicked.connect(self.deactivateSegment)
        
        self.splitButton = QPushButton("Download", self)
        self.splitButton.clicked.connect(lambda: self.splitVideo(merge=False))
        
        self.mergeButton = QPushButton("Merge & Download", self)
        self.mergeButton.clicked.connect(lambda: self.splitVideo(merge=True))
        
        self.backPoint1Button = QPushButton("<< .1s", self)
        self.backPoint1Button.clicked.connect(lambda: self.seek(-0.1))
        
        self.back5Button = QPushButton("<< 5s", self)
        self.back5Button.clicked.connect(lambda: self.seek(-5))
        
        self.back10Button = QPushButton("<< 10s", self)
        self.back10Button.clicked.connect(lambda: self.seek(-10))
        
        self.pauseButton = QPushButton("Pause", self)
        self.pauseButton.clicked.connect(self.togglePause)
        
        self.forward5Button = QPushButton("5s >>", self)
        self.forward5Button.clicked.connect(lambda: self.seek(5))
        
        self.forward10Button = QPushButton("10s >>", self)
        self.forward10Button.clicked.connect(lambda: self.seek(10))
        
        self.forwardPoint1Button = QPushButton(".1s >>", self)
        self.forwardPoint1Button.clicked.connect(lambda: self.seek(0.1))
        
        self.gotoButton = QPushButton("Goto", self)
        self.gotoButton.clicked.connect(self.showGotoDialog)
        
        self.undoButton = QPushButton("Undo", self)
        self.undoButton.clicked.connect(self.undoAction)
        
        self.redoButton = QPushButton("Redo", self)
        self.redoButton.clicked.connect(self.redoAction)
        
        self.clipStartLabel = QLabel("0.0 - 0.0")
        self.clipEndLabel = QLabel("| D: 0.0s")
        clipInfoLayout = QHBoxLayout()
        clipInfoLayout.addWidget(self.clipStartLabel)
        clipInfoLayout.addWidget(self.clipEndLabel)
        clipInfoLayout.addStretch()
        
        self.progressBar = QProgressBar(self)
        self.progressBar.setStyleSheet("QProgressBar { height: 8px; } QProgressBar::chunk { background-color: blue; }")
        self.progressBar.setMinimum(0)
        # Remove setMaximum(100) - set dynamically in splitVideo
        self.progressBar.setValue(0)
        self.progressBar.setVisible(False)
        
        timeContainer = QWidget(self)
        timeContainerLayout = QStackedLayout(timeContainer)
        timeContainerLayout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        timeContainerLayout.setContentsMargins(0, 0, 0, 0)
        
        timeContainer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        self.slider = QSlider(Qt.Orientation.Horizontal, timeContainer)
        self.slider.setMinimum(0)
        self.slider.setMaximum(100)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.slider.setMinimumHeight(25)
        self.slider.sliderReleased.connect(self.sliderReleased)
        
        self.thumb_width = 5
        
        splitContainer = QWidget(timeContainer)
        splitLayout = QHBoxLayout(splitContainer)
        splitLayout.setContentsMargins(0, 0, 0, 0)
        splitLayout.setSpacing(0)
        
        self.splitSlider = QLabel(splitContainer)
        self.splitSlider.setStyleSheet("background: transparent;")
        self.splitSlider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.splitSlider.setFixedHeight(20)
        
        splitLayout.addSpacerItem(QSpacerItem(self.thumb_width - 1, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum))
        splitLayout.addWidget(self.splitSlider)
        
        timeContainerLayout.addWidget(self.slider)
        timeContainerLayout.addWidget(splitContainer)
        
        def resizeOverlay(event):
            groove_width = self.slider.width() - 2 * self.thumb_width - 3
            self.splitSlider.setFixedWidth(groove_width)
        
        self.slider.resizeEvent = resizeOverlay
        
        self.currentTimeLabel = QLabel("0:00:00.0")
        self.totalTimeLabel = QLabel("0:00:00.0")
        
        timeLayout = QHBoxLayout()
        timeLayout.addWidget(self.currentTimeLabel)
        timeLayout.addWidget(timeContainer, stretch=1)
        timeLayout.addWidget(self.totalTimeLabel)
        
        controlsLayout = QHBoxLayout()
        controlsLayout.addWidget(self.backPoint1Button)
        controlsLayout.addWidget(self.back10Button)
        controlsLayout.addWidget(self.back5Button)
        controlsLayout.addWidget(self.pauseButton)
        controlsLayout.addWidget(self.forward5Button)
        controlsLayout.addWidget(self.forward10Button)
        controlsLayout.addWidget(self.forwardPoint1Button)
        controlsLayout.addWidget(self.gotoButton)
        controlsLayout.addWidget(self.undoButton)
        controlsLayout.addWidget(self.redoButton)
        
        layout = QVBoxLayout()
        layout.addWidget(self.openButton)
        layout.addWidget(self.videoContainer, stretch=1)
        layout.addLayout(clipInfoLayout)
        layout.addLayout(timeLayout)
        layout.addLayout(controlsLayout)
        layout.addWidget(self.addSplitButton)
        layout.addWidget(self.deactivateButton)
        layout.addWidget(self.mergeButton)
        layout.addWidget(self.splitButton)
        layout.addWidget(self.progressBar)
        self.setLayout(layout)
        
        self.updateGeometry()
        self.show()
        
        self.mediaPlayer.positionChanged.connect(self.updateSliderPosition)
        self.mediaPlayer.positionChanged.connect(self.updateClipInfo)
        
        self.full_ui_setup = True

    def undoAction(self):
        if self.split_points:
            last_action = self.split_points.pop()
            self.redo_stack.append(last_action)
            self.updateSplitOverlay()
    
    def redoAction(self):
        if self.redo_stack:
            last_redo = self.redo_stack.pop()
            self.split_points.append(last_redo)
            self.updateSplitOverlay()
    
    def deactivateSegment(self):
        position = self.mediaPlayer.position() / 1000
        nearest_splits = sorted(self.split_points + [0, self.frame_count / self.fps])
        for i in range(len(nearest_splits) - 1):
            if nearest_splits[i] <= position < nearest_splits[i + 1]:
                segment = (nearest_splits[i], nearest_splits[i + 1])
                if segment in self.deactivated_segments:
                    self.deactivated_segments.remove(segment)
                else:
                    self.deactivated_segments.append(segment)
                self.updateSplitOverlay()
                break
    
    def updateSliderPosition(self, position):
        if self.frame_count > 0 and self.mediaPlayer.duration() > 0:
            frame_number = int((position / self.mediaPlayer.duration()) * self.frame_count)
            self.slider.setValue(frame_number)
            self.currentTimeLabel.setText(self.formatTime(position / 1000))
    
    def formatTime(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 10)
        return f"{hours}:{minutes:02}:{secs:02}.{millis}"
    
    def formatTimeCompact(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02}:{secs:02.1f}"
        elif minutes > 0:
            return f"{minutes}:{secs:02.1f}"
        else:
            return f"{secs:.1f}"
        
    def formatDuration(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60  # Keep as float for decimal
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or hours > 0:  # Show minutes if hours exist, even if 0
            parts.append(f"{minutes}m")
        parts.append(f"{secs:.1f}s")
        return " ".join(parts)

    def updateDurationFromPlayer(self):
        duration = self.mediaPlayer.duration() / 1000  # Convert ms to seconds
        if duration > 0:
            self.frame_count = int(duration * self.fps)  # Estimate frames
            self.slider.setMaximum(self.frame_count)
            self.totalTimeLabel.setText(self.formatTime(duration))
        else:
            print("Warning: Could not determine video duration from QMediaPlayer")

    class VideoProcessor(QThread):
        finished = pyqtSignal(str)
        error = pyqtSignal(str)
        
        def __init__(self, file_path):
            super().__init__()
            self.file_path = file_path
        
        def run(self):
            temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
            try:
                # Build FFmpeg command manually
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-i", self.file_path,
                    "-vf", "scale=-2:1920,crop=1080:1920",
                    "-vcodec", "libx264",
                    "-acodec", "aac",
                    "-pix_fmt", "yuv420p",
                    "-preset", "veryfast",
                    "-f", "mp4",
                    "-y",  # Overwrite output
                    temp_output
                ]
                # Hide FFmpeg console on Windows
                creation_flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
                subprocess.run(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    creationflags=creation_flags
                )
                print(f"VideoProcessor: Processed {temp_output}")
                self.finished.emit(temp_output)
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.decode() if e.stderr else "Unknown FFmpeg error"
                print(f"VideoProcessor Error: {error_msg}")
                self.error.emit(error_msg)
                if os.path.exists(temp_output):
                    os.remove(temp_output)

    class DownloadProcessor(QThread):
        progress = pyqtSignal(int)
        finished = pyqtSignal(int)
        error = pyqtSignal(str)
        
        def __init__(self, video_path, original_path, split_points, deactivated_segments, merge):
            super().__init__()
            self.video_path = video_path
            self.original_path = original_path
            self.split_points = split_points
            self.deactivated_segments = deactivated_segments
            self.merge = merge
            self.frame_count = None
            self.fps = None
        
        def run(self):
            try:
                source_dir = os.path.dirname(self.original_path)
                source_name, _ = os.path.splitext(os.path.basename(self.original_path))
                output_folder = os.path.join(source_dir, source_name)
                os.makedirs(output_folder, exist_ok=True)

                split_times = sorted([0] + self.split_points + [self.frame_count / self.fps])
                active_segments = []
                for i in range(len(split_times) - 1):
                    segment = (split_times[i], split_times[i + 1])
                    if segment not in self.deactivated_segments:
                        active_segments.append(segment)

                if not active_segments:
                    self.finished.emit(0)
                    return

                creation_flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

                if self.merge:
                    merged_file_path = os.path.join(output_folder, f"{source_name}_merged.mp4")
                    temp_list = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
                    file_list_path = temp_list.name

                    split_files = []
                    for i, (start, end) in enumerate(active_segments):
                        end -= 0.1
                        segment_path = os.path.join(output_folder, f"{i+1}.mp4")
                        split_files.append(segment_path)

                        duration = end - start
                        print(f"Cutting segment {i+1}: {start:.1f}s - {end:.1f}s, Duration: {duration:.1f}s")
                        ffmpeg_cmd = [
                            "ffmpeg",
                            "-i", self.video_path,
                            "-ss", str(start),
                            "-t", str(duration),
                            "-vcodec", "libx264",
                            "-acodec", "aac",
                            "-f", "mp4",
                            "-y",
                            segment_path
                        ]
                        subprocess.run(
                            ffmpeg_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            check=True,
                            creationflags=creation_flags
                        )
                        temp_list.write(f"file '{segment_path}'\n".encode())
                        self.progress.emit(i + 1)

                    temp_list.close()

                    print(f"Merging {len(active_segments)} segments into {merged_file_path}")
                    ffmpeg_cmd = [
                        "ffmpeg",
                        "-f", "concat",
                        "-safe", "0",
                        "-i", file_list_path,
                        "-c", "copy",
                        "-y",
                        merged_file_path
                    ]
                    subprocess.run(
                        ffmpeg_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                        creationflags=creation_flags
                    )

                    os.remove(file_list_path)
                    for part in split_files:
                        os.remove(part)
                else:
                    for i, (start, end) in enumerate(active_segments):
                        end -= 0.1
                        output_path = os.path.join(output_folder, f"{i+1}.mp4")
                        duration = end - start
                        print(f"Extracting segment {i+1}: {start:.1f}s - {end:.1f}s, Duration: {duration:.1f}s")
                        ffmpeg_cmd = [
                            "ffmpeg",
                            "-i", self.video_path,
                            "-ss", str(start),
                            "-t", str(duration),
                            "-vcodec", "libx264",
                            "-acodec", "aac",
                            "-f", "mp4",
                            "-y",
                            output_path
                        ]
                        subprocess.run(
                            ffmpeg_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            check=True,
                            creationflags=creation_flags
                        )
                        self.progress.emit(i + 1)

                self.finished.emit(len(active_segments))
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.decode() if e.stderr else "Unknown FFmpeg error"
                print(f"DownloadProcessor Error: {error_msg}")
                self.error.emit(error_msg)

    def update_loading_text(self):
        base_text = "Loading"
        dots = "." * (self.loading_state % 4)
        self.loading_label.setText(base_text + dots)
        self.loading_state += 1

    def update_download_text(self):
        base_text = "Downloading"
        dots = "." * (self.download_state % 4)  # 0, 1, 2, 3 dots
        if self.active_download_button:
            self.active_download_button.setText(base_text + dots)
        self.download_state += 1

    def openFile(self):
        options = QFileDialog.Option.ReadOnly
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video File", "", "Video Files (*.mp4 *.avi *.mov)", options=options)
        
        if file_path:
            # Clean up previous temp file if it exists
            if self.video_path and os.path.exists(self.video_path):
                os.remove(self.video_path)
            
            # Hide button, show loading
            self.openButton.hide()
            self.loading_label.show()
            self.loading_timer.start(1000)  # 1-second interval
            
            # Start processing in thread
            self.processor = self.VideoProcessor(file_path)
            self.processor.finished.connect(self.on_processing_finished)
            self.processor.error.connect(self.on_processing_error)
            self.processor.start()
            self.original_video_path = file_path  # Store original path

    def on_processing_finished(self, temp_output):
        self.loading_timer.stop()
        self.loading_label.hide()
        
        self.video_path = temp_output
        self.cap = cv2.VideoCapture(self.video_path)
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = int(self.cap.get(cv2.CAP_PROP_FPS))
        print(f"Processed 9:16 video: {temp_output}, Size: {os.path.getsize(temp_output)} bytes")
        print(f"Frame count: {self.frame_count}, FPS: {self.fps}")
        
        self.setupFullUI()
        self.mediaPlayer.setSource(QUrl.fromLocalFile(self.video_path))
        if self.frame_count <= 0:
            QTimer.singleShot(100, self.updateDurationFromPlayer)
        else:
            self.slider.setMaximum(self.frame_count)
            total_time = self.frame_count / self.fps
            self.totalTimeLabel.setText(self.formatTime(total_time))
        self.mediaPlayer.play()
        
        self.split_points = []
        self.deactivated_segments = []
        self.undo_stack = []
        self.redo_stack = []
        self.updateSplitOverlay()
        self.updateClipInfo(0)
        
        # Make window fullscreen
        self.showMaximized()

    def on_processing_error(self, error_message):
        self.loading_timer.stop()
        self.loading_label.hide()
        self.openButton.show()
        print(f"Video Loading Error: {error_message}")
        QMessageBox.critical(self, "Error", "Failed to process video to 9:16.")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'videoContainer'):
            self.centerPlayPauseIcon()

    def sliderReleased(self):
        if self.mediaPlayer:
            position = (self.slider.value() / self.frame_count) * self.mediaPlayer.duration()
            self.mediaPlayer.setPosition(int(position))
            self.updateClipInfo(int(position))  # Update clip info after manual slide

    def addSplitPoint(self):
        position = self.mediaPlayer.position() / 1000  # Convert to seconds
        self.split_points.append(position)
        self.split_points.sort()  # Keep split points ordered
        self.undo_stack.append(('split', position))
        self.redo_stack.clear()  # Clear redo stack when new action is performed
        self.updateSplitOverlay()
        self.updateClipInfo(int(position * 1000))

    def updateSplitOverlay(self):
        if not self.splitSlider.width():
            return
            
        pixmap = QPixmap(self.splitSlider.width(), self.splitSlider.height())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        
        # Draw deactivated segments
        painter.setBrush(QColor(200, 100, 100, 150))
        for start, end in self.deactivated_segments:
            x_start = int((start / (self.frame_count / self.fps)) * self.splitSlider.width())
            x_end = int((end / (self.frame_count / self.fps)) * self.splitSlider.width())
            painter.drawRect(x_start, 0, x_end - x_start, self.splitSlider.height())
        
        # Draw split points
        painter.setPen(QColor(0, 0, 255))
        painter.setBrush(QColor(0, 0, 255))
        for split in self.split_points:
            x_pos = int((split / (self.frame_count / self.fps)) * self.splitSlider.width())
            painter.drawRect(x_pos-1, 0, 1, self.splitSlider.height())
        
        painter.end()
        self.splitSlider.setPixmap(pixmap)

    def updateClipInfo(self, position):
        if not self.video_path or self.frame_count <= 0:
            self.clipStartLabel.setText("0.0 - 0.0")
            self.clipEndLabel.setText("| D: 0.0s")
            return

        # Convert position from milliseconds to seconds
        current_time = position / 1000

        # Get all split points including video start and end
        split_times = sorted([0] + self.split_points + [self.frame_count / self.fps])

        # Find the clip boundaries (nearest split points)
        clip_start = 0
        clip_end = self.frame_count / self.fps
        for i in range(len(split_times) - 1):
            if split_times[i] <= current_time < split_times[i + 1]:
                clip_start = split_times[i]
                clip_end = split_times[i + 1] - 0.1
                break

        # Calculate duration
        clip_duration = clip_end - clip_start

        # Update labels with different formats
        self.clipStartLabel.setText(f"{self.formatTimeCompact(clip_start)} - {self.formatTimeCompact(clip_end)}")
        self.clipEndLabel.setText(f"| D: {self.formatDuration(clip_duration)}")

    class GotoDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Go to Time")
            self.setModal(True)
            self.initUI()

        def initUI(self):
            layout = QVBoxLayout()

            # Time input fields: hours, minutes, seconds, milliseconds
            timeLayout = QHBoxLayout()
            self.hoursEdit = QLineEdit("")
            self.hoursEdit.setFixedWidth(40)
            self.hoursEdit.setPlaceholderText("h")
            self.minutesEdit = QLineEdit("")
            self.minutesEdit.setFixedWidth(40)
            self.minutesEdit.setPlaceholderText("m")
            self.secondsEdit = QLineEdit("")
            self.secondsEdit.setFixedWidth(40)
            self.secondsEdit.setPlaceholderText("s")
            self.millisEdit = QLineEdit("")
            self.millisEdit.setFixedWidth(40)
            self.millisEdit.setPlaceholderText("ms")

            timeLayout.addWidget(self.hoursEdit)
            timeLayout.addWidget(QLabel(":"))
            timeLayout.addWidget(self.minutesEdit)
            timeLayout.addWidget(QLabel(":"))
            timeLayout.addWidget(self.secondsEdit)
            timeLayout.addWidget(QLabel("."))
            timeLayout.addWidget(self.millisEdit)
            layout.addLayout(timeLayout)

            # Buttons with spacing
            buttonLayout = QHBoxLayout()
            self.goButton = QPushButton("Go", self)
            self.goButton.clicked.connect(self.accept)
            self.cancelButton = QPushButton("Cancel", self)
            self.cancelButton.clicked.connect(self.reject)
            
            buttonLayout.addWidget(self.goButton)
            buttonLayout.addStretch()
            buttonLayout.addWidget(self.cancelButton)
            layout.addLayout(buttonLayout)

            # Set the layout first
            self.setLayout(layout)

            # Set tab order after layout is applied
            self.setTabOrder(self.hoursEdit, self.minutesEdit)
            self.setTabOrder(self.minutesEdit, self.secondsEdit)
            self.setTabOrder(self.secondsEdit, self.millisEdit)
            self.setTabOrder(self.millisEdit, self.hoursEdit)

            # Set focus to hoursEdit when dialog opens
            self.hoursEdit.setFocus()

        def getTime(self):
            # Convert inputs to floats, default to 0 if empty or invalid
            try:
                hours = float(self.hoursEdit.text() or 0)
            except ValueError:
                hours = 0
            try:
                minutes = float(self.minutesEdit.text() or 0)
            except ValueError:
                minutes = 0
            try:
                seconds = float(self.secondsEdit.text() or 0)
            except ValueError:
                seconds = 0
            try:
                millis = float(self.millisEdit.text() or 0) / 10  # Convert to seconds (e.g., 1 -> 0.1s)
            except ValueError:
                millis = 0
            total_seconds = hours * 3600 + minutes * 60 + seconds + millis
            return total_seconds

    def showGotoDialog(self):
        dialog = self.GotoDialog(self)
        if dialog.exec():  # If "Go" is clicked (accept)
            time_seconds = dialog.getTime()
            # Convert to milliseconds and seek
            if self.mediaPlayer and self.frame_count > 0:
                position_ms = int(time_seconds * 1000)
                # Clamp to video duration
                duration_ms = self.mediaPlayer.duration()
                position_ms = max(0, min(position_ms, duration_ms))
                self.mediaPlayer.setPosition(position_ms)
                # Update slider manually
                frame_number = int((position_ms / duration_ms) * self.frame_count)
                self.slider.setValue(frame_number)

    def togglePlayPauseOnClick(self, event):
        if self.mediaPlayer:
            if self.mediaPlayer.isPlaying():
                self.mediaPlayer.pause()
                self.pauseButton.setText("Play")
                self.showPlayPauseIcon("play")
            else:
                self.mediaPlayer.play()
                self.pauseButton.setText("Pause")
                self.showPlayPauseIcon("pause")

    def showPlayPauseIcon(self, state):
        """Show play or pause icon with smooth fadeout animation."""
        if not hasattr(self, 'floatingOverlay'):
            from PyQt6.QtWidgets import QWidget
            self.floatingOverlay = QWidget()
            self.floatingOverlay.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
            self.floatingOverlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.playPauseIcon = QLabel(self.floatingOverlay)
            self.playPauseIcon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.playPauseIcon.setStyleSheet("background: transparent;")
            self.playPauseIcon.setFixedSize(64, 64)
            self.fadeAnimation = QPropertyAnimation(self.floatingOverlay, b"windowOpacity")
            self.fadeAnimation.setDuration(500)
            self.fadeAnimation.setStartValue(1.0)
            self.fadeAnimation.setEndValue(0.0)
            self.fadeAnimation.finished.connect(self.floatingOverlay.hide)
        
        # Load icon
        base_path = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_path, "resources", "images", f"{state}.png")
        
        pixmap = QPixmap(icon_path)
        if pixmap.isNull():
            print(f"Error: Could not load icon at {icon_path}")
            return
        
        pixmap = pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.playPauseIcon.setPixmap(pixmap)
        
        # Position overlay over videoContainer with precise centering
        container_rect = self.videoContainer.rect()  # Local coordinates
        global_pos = self.videoContainer.mapToGlobal(container_rect.topLeft())  # Convert to global
        overlay_x = global_pos.x() + (self.videoContainer.width() - 64) // 2
        overlay_y = global_pos.y() + (self.videoContainer.height() - 64) // 2
        self.floatingOverlay.setGeometry(overlay_x, overlay_y, 64, 64)
        self.playPauseIcon.move(0, 0)
        
        self.floatingOverlay.setWindowOpacity(1.0)
        self.floatingOverlay.show()
        self.fadeAnimation.start()

    def centerPlayPauseIcon(self):
        if hasattr(self, 'playPauseIcon') and self.videoContainer.width() > 0 and self.videoContainer.height() > 0:
            icon_width = self.playPauseIcon.width()
            icon_height = self.playPauseIcon.height()
            x = (self.videoContainer.width() - icon_width) // 2
            y = (self.videoContainer.height() - icon_height) // 2
            self.playPauseIcon.move(x, y)

    def togglePause(self):
        if self.mediaPlayer.isPlaying():
            self.mediaPlayer.pause()
            self.pauseButton.setText("Play")
            self.showPlayPauseIcon("play")
        else:
            self.mediaPlayer.play()
            self.pauseButton.setText("Pause")
            self.showPlayPauseIcon("pause")

    def seek(self, seconds):
        if self.mediaPlayer:
            current_pos = self.mediaPlayer.position()
            new_position = max(0, current_pos + (seconds * 1000))
            self.mediaPlayer.setPosition(int(new_position))

    def splitVideo(self, merge=False):
        if not self.video_path or not self.split_points:
            return

        # Determine which button was clicked
        self.active_download_button = self.mergeButton if merge else self.splitButton
        
        # Calculate active segments
        split_times = sorted([0] + self.split_points + [self.frame_count / self.fps])
        active_segments = [seg for i, seg in enumerate(zip(split_times[:-1], split_times[1:])) if seg not in self.deactivated_segments]
        
        if not active_segments:
            return
        
        # Set progress bar maximum to number of segments
        self.progressBar.setMaximum(len(active_segments))
        self.active_download_button.setEnabled(False)
        self.download_timer.start(1000)
        self.progressBar.setVisible(True)

        # Start processing in thread
        self.download_processor = self.DownloadProcessor(
            self.video_path, self.original_video_path, self.split_points.copy(),
            self.deactivated_segments.copy(), merge
        )
        self.download_processor.frame_count = self.frame_count
        self.download_processor.fps = self.fps
        self.download_processor.progress.connect(self.update_progress)
        self.download_processor.finished.connect(self.on_download_finished)
        self.download_processor.error.connect(self.on_download_error)
        self.download_processor.start()

    def update_progress(self, value):
        self.progressBar.setValue(value)

    def on_download_finished(self, num_files):
        self.download_timer.stop()
        self.active_download_button.setText("Merge & Download" if self.active_download_button == self.mergeButton else "Download")
        self.active_download_button.setEnabled(True)
        self.progressBar.setVisible(False)
        if num_files > 0:
            QMessageBox.information(self, "Success", f"Video processing completed! Processed {num_files} segment{'s' if num_files != 1 else ''}.")

    def on_download_error(self, error_message):
        self.download_timer.stop()
        self.active_download_button.setText("Merge & Download" if self.active_download_button == self.mergeButton else "Download")
        self.active_download_button.setEnabled(True)
        self.progressBar.setVisible(False)
        print(f"An error occurred: {error_message}")
        QMessageBox.critical(self, "Error", "An error occurred while processing the video.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoEditorApp()
    window.show()
    sys.exit(app.exec())