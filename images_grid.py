"""Card-grid presentation; the existing Images session owns every operation."""
from PySide6 import QtCore, QtGui, QtWidgets

from images_workspace import ImageTab as _ImagesWorkspace
from project_model import PAGE_TYPES, MAX_PAGES
from prompt_writer_project import page_label


class _ArtworkDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        palette = option.palette
        background = palette.highlight().color() if selected else palette.base().color()
        foreground = palette.highlightedText().color() if selected else palette.text().color()
        painter.setBrush(background)
        painter.setPen(QtGui.QPen(palette.highlight().color() if selected else palette.mid().color(), 2 if selected else 1))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(foreground)
        painter.drawText(rect.adjusted(12, 7, -12, -rect.height()+32), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, str(index.row()+1))
        art = rect.adjusted(10, 36, -10, -56)
        icon = index.data(QtCore.Qt.DecorationRole)
        if icon is not None and not icon.isNull():
            icon.paint(painter, art, QtCore.Qt.AlignCenter)
        else:
            painter.drawText(art, QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, index.data(QtCore.Qt.UserRole+1) or 'Artwork missing')
        painter.drawText(rect.adjusted(12, rect.height()-49, -12, -25), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, painter.fontMetrics().elidedText(index.data(QtCore.Qt.DisplayRole) or '', QtCore.Qt.ElideRight, rect.width()-24))
        status = index.data(QtCore.Qt.UserRole+2) or ''
        if status != 'Ready':
            painter.drawText(rect.adjusted(12, rect.height()-25, -12, -5), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, painter.fontMetrics().elidedText(status, QtCore.Qt.ElideRight, rect.width()-24))
        painter.restore()


class ImageTab(_ImagesWorkspace):
    def __init__(self, *args, **kwargs):
        self._grid_ready = False
        super().__init__(*args, **kwargs)
        root = self.layout()
        while root.count():
            item = root.takeAt(0)
            if item.widget():
                item.widget().hide()
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.heading)
        top.addStretch()
        top.addWidget(self.add_button)
        root.addLayout(top)
        bar = QtWidgets.QHBoxLayout()
        for widget in (self.duplicate_button, self.delete_button, self.undo_button):
            bar.addWidget(widget)
        bar.addStretch()
        self.up_button.setText('Move left')
        self.down_button.setText('Move right')
        bar.addWidget(self.up_button)
        bar.addWidget(self.down_button)
        root.addLayout(bar)
        self.page_list.setViewMode(QtWidgets.QListView.IconMode)
        self.page_list.setFlow(QtWidgets.QListView.LeftToRight)
        self.page_list.setWrapping(True)
        self.page_list.setMovement(QtWidgets.QListView.Snap)
        self.page_list.setResizeMode(QtWidgets.QListView.Adjust)
        self.page_list.setSpacing(4)
        self.page_list.setUniformItemSizes(True)
        self.page_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.page_list.setItemDelegate(_ArtworkDelegate(self.page_list))
        self.page_list.setAccessibleName('Image cards; select a page, drag to reorder, or use Move left and Move right')
        root.addWidget(self.page_list, 1)
        footer = QtWidgets.QHBoxLayout()
        fields = QtWidgets.QVBoxLayout()
        identity = QtWidgets.QHBoxLayout()
        for text, control, stretch in [('Name', self.name_edit, 1), ('Type', self.type_combo, 0)]:
            column = QtWidgets.QVBoxLayout()
            column.addWidget(QtWidgets.QLabel(text))
            column.addWidget(control)
            identity.addLayout(column, stretch)
        self.type_combo.setMinimumWidth(130)
        fields.addLayout(identity)
        actions = QtWidgets.QHBoxLayout()
        for widget in (self.import_button, self.stock_button, self.crop_button, self.more_button):
            actions.addWidget(widget)
        actions.addStretch()
        fields.addLayout(actions)
        animations = QtWidgets.QHBoxLayout()
        animations.addWidget(self.animation_mode)
        animations.addWidget(self.animation_button)
        animations.addStretch()
        fields.addLayout(animations)
        fields.addStretch()
        footer.addLayout(fields, 1)
        footer.addWidget(self.pwrite_fab)
        self.pwrite_fab.set_surface(self)
        root.addLayout(footer)
        root.addWidget(self.status)
        self._footer = footer
        self._identity = identity
        self._grid_ready = True
        for widget in (self.heading, self.add_button, self.duplicate_button, self.delete_button,
                       self.up_button, self.down_button, self.page_list, self.name_edit,
                       self.type_combo, self.import_button, self.stock_button, self.crop_button,
                       self.more_button, self.pwrite_fab, self.status):
            widget.show()
        self.refresh_cards()
        self._update_workspace_layout()

    def refresh_cards(self):
        result = super().refresh_cards()
        if not self._grid_ready:
            return result
        for row, page in enumerate(self.session.project.pages):
            item = self.page_list.item(row)
            status = item.text().split('\n')[-1]
            if status == 'Message empty':
                status = 'Ready'
            item.setData(QtCore.Qt.UserRole+2, status)
            item.setText(page_label(self.session.project, page['id']))
            kind = PAGE_TYPES[page['type']].label
            missing = 'Final page missing' if page['type'] == 'end_page' else kind + ' missing'
            item.setData(QtCore.Qt.UserRole+1, missing)
            item.setToolTip(item.text() + '\n' + status)
            item.setData(QtCore.Qt.AccessibleTextRole, f'{row+1}, {item.text()}')
        self.limit_label.hide()
        self.add_button.setEnabled(True)
        self.add_button.setToolTip('Add a page after the selected image')
        self.duplicate_button.setEnabled(self.session.selected_page_id is not None)
        self.undo_button.setVisible(bool(self._undo))
        self.import_button.setText('Import')
        self.stock_button.setText('Stock')
        self.crop_button.setText('Crop')
        self._size_grid()
        return result

    def _size_grid(self):
        if not self._grid_ready:
            return
        width = max(160, self.page_list.viewport().width()-12)
        columns = max(1, min(3, width // 240))
        cell_width = max(150, width // columns-8)
        cell_height = max(210, min(380, int(cell_width*1.09)+65))
        if self.page_list.count() <= columns:
            cell_height = max(180, min(cell_height, self.page_list.viewport().height()-12))
        size = QtCore.QSize(cell_width, cell_height)
        self.page_list.setGridSize(size)
        self.page_list.setIconSize(QtCore.QSize(cell_width-28, cell_height-100))
        for row in range(self.page_list.count()):
            self.page_list.item(row).setSizeHint(size)

    def _update_workspace_layout(self):
        if not self._grid_ready:
            return
        self.pwrite_fab.set_artwork_size(112 if self.width()<720 else 144)
        self._identity.setDirection(QtWidgets.QBoxLayout.TopToBottom if self.width()<540 else QtWidgets.QBoxLayout.LeftToRight)
        self._size_grid()

    def add_page(self, kind):
        if len(self.session.project.pages)>=MAX_PAGES:
            raise ValueError('You’ve reached the maximum of 10 pages.')
        return super().add_page(kind)

    def duplicate_page(self, page_id):
        if len(self.session.project.pages)>=MAX_PAGES:
            raise ValueError('You’ve reached the maximum of 10 pages.')
        return super().duplicate_page(page_id)
