# Written by Egbert Bouman
# see LICENSE for license information

import sys
import wx
import wx.lib.agw.gradientbutton as gb
import wx.lib.mixins.listctrl as listmix
import Tribler.Main.vwxGUI.ChessImages as ChessImages

from Tribler.Utilities.TimedTaskQueue import TimedTaskQueue

class ChessSubPanel(wx.Panel):
    """Panel subclass for displaying smaller panels within the chess application"""

    def __init__(self, parent, title = "", center = False, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)
        self.title = title
        self.center = center
        self.font = self.GetFont()
        self.fontbold = self.GetFont()
        self.fontbold.SetWeight(wx.FONTWEIGHT_BOLD)
        self.edgeColour = wx.Colour(169,183,188)
        self.parentColour = wx.Colour(216,233,240)
        self.SetBackgroundColour(wx.Colour(192,208,214))
        self.SetForegroundColour(wx.BLACK)
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)

    def OnEraseBackground(self, event):
        pass

    def OnSize(self, event):
        self.Refresh()
        event.Skip()

    # When subclasses are created from ChessSubPanel, this method can be used for further
    # drawing (this way there is no need to overwrite the onPaint method).
    def Draw(self, dc):
        pass

    def OnPaint(self, event):
        # Paint the panel with rounded corners and an optional (underlined) title
        dc = wx.BufferedPaintDC(self)
        gc = wx.GraphicsContext.Create(dc)
        dc.SetBackground(wx.Brush(self.parentColour))
        dc.Clear()
        x, y, width, height = self.GetClientRect()
        path = gc.CreatePath()
        path.AddRoundedRectangle(x, y, width-1, height-1, 4)
        path.CloseSubpath()
        gc.SetPen(wx.Pen(self.edgeColour, 1, wx.SOLID))
        gc.SetBrush(wx.Brush(self.GetBackgroundColour(), wx.SOLID))
        gc.DrawPath(path)
        if self.title != "":
            dc.SetFont(self.fontbold)
            dc.SetTextForeground(self.GetForegroundColour())
            x, y = (10, 10)
            if self.center:
                textWidth = dc.GetFullTextExtent(self.title)[0]
                x = (width-textWidth)/2
            dc.DrawText(self.title, x, y)
            dc.SetPen(wx.Pen(self.GetForegroundColour(), 2))
            dc.DrawLine(10, 25, width-10, 25)
        self.Draw(dc)


class ChessGradientButton(gb.GradientButton):
    """GradientButton subclass that allows for the corner-radius to be passed to its __init__ method"""

    def __init__(self, parent, radius = 0, *args, **kwargs):
        gb.GradientButton.__init__(self, parent, *args, **kwargs)
        self.radius = radius
        if sys.platform == 'linux2':
            font = self.GetFont()
            font.SetPointSize(font.GetPointSize()+2)
            self.SetFont(font)

    def GetPath(self, gc, rc, r):
        return gb.GradientButton.GetPath(self, gc, rc, self.radius)


class ChessList(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin, listmix.ColumnSorterMixin):
    """ListCtrl subclass for displaying various lists within the chess application"""

    def __init__(self, parent, columns = {}, data = {}, filtered = False, *args, **kwargs):
        wx.ListCtrl.__init__(self, parent, -1, style=wx.LC_REPORT|wx.LC_VIRTUAL|wx.SUNKEN_BORDER, *args, **kwargs)

        # Add sorting icons
        self.il = wx.ImageList(16, 16)
        # Empty bitmap is used by ClearColumnImage
        self.idx_empty = self.il.Add(wx.EmptyBitmapRGBA(16, 16, 0, 0, 0, 0) )
        self.idx_up = self.il.Add(ChessImages.getArrow_upBitmap())
        self.idx_dn = self.il.Add(ChessImages.getArrow_dnBitmap())
        self.SetImageList(self.il, wx.IMAGE_LIST_SMALL)

        # Add attributes (in order to get alternating background colours for each row)
        self.attr1 = wx.ListItemAttr()
        self.attr1.SetBackgroundColour(wx.Colour(240,248,255))
        self.attr2 = wx.ListItemAttr()
        self.attr2.SetBackgroundColour(wx.Colour(240,255,204))

        # Add columns
        for key,column in columns.iteritems():
            self.InsertColumn(key, column[0], column[1], column[2])

        # Add data
        self.itemDataMap = data
        self.itemIndexMap = data.keys()
        self.SetItemCount(len(data))
        self.filtered = filtered
        if filtered: self.filteredItemIndexMap = data.keys()

        # Automatically resize the last column & enable sorting
        listmix.ListCtrlAutoWidthMixin.__init__(self)
        listmix.ColumnSorterMixin.__init__(self, 5)

        # By default sort on column 0, in ascending order
        self.SortListItems(0, 1)

        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.OnItemActivated)

    # Workaround to fix alignment issues with column-headers
    def ClearColumnImage(self, col):
        self.SetColumnImage(col, 0)

    # Next three methods are used for sorting
    def SortItems(self,sorter=cmp):
        items = list(self.itemIndexMap)
        items.sort(sorter)
        self.itemIndexMap = items
        if self.filtered:
	    items = list(self.filteredItemIndexMap)
            items.sort(sorter)
            self.filteredItemIndexMap = items
        self.Refresh()

    def GetListCtrl(self):
        return self

    def GetSortImages(self):
        return (self.idx_dn, self.idx_up)

    # Sets and refreshes all cells within the list
    def SetData(self, data):
        self.itemDataMap = data
        if self.filtered:
            self.filteredItemIndexMap = data.keys()
        self.itemIndexMap = data.keys()
        self.SetItemCount(len(data))
        self.SortListItems(*self.GetSortState())
        self.Refresh()

    # Sets and refreshes a single cell within the list
    def SetCell(self, index, col, text):
        # The item does not exist
        if index not in self.itemIndexMap:
            return
        # Set the new content
        self.itemDataMap[index][col] = text

        # Calculate the cell's rect and refresh
        if self.filtered:
            item = self.filteredItemIndexMap.index(index)
        else:
            item = self.itemIndexMap.index(index)
        (x,y,width,height) = self.GetItemRect(item)
        for c in range(self.GetColumnCount()):
            if c == col:
                continue
	    else:
	        width -= self.GetColumnWidth(c)
	        x += self.GetColumnWidth(c)
        self.RefreshRect(wx.Rect(x,y,width,height),False)

    # Simple method for filtering the list
    def Filter(self, text, cols):
        if not self.filtered: return
        self.filteredItemIndexMap = []
        for index in self.itemIndexMap:
            append = False
            for col in cols:
                if text.lower() in self.itemDataMap[index][col].lower() and not append:
                    self.filteredItemIndexMap.append(index)
                    append = True
        self.SetItemCount(len(self.filteredItemIndexMap))
        self.Refresh()

    # When an item is activated, do something
    def OnItemActivated(self, event):
        pass

    # Remaing methods are callbacks for the virtual list
    def OnGetItemText(self, item, col):
        if self.filtered: index = self.filteredItemIndexMap[item]
        else: index = self.itemIndexMap[item]
        return self.itemDataMap[index][col]

    def OnGetItemImage(self, item):
        return self.OnGetItemColumnImage(item, 0)

    def OnGetItemColumnImage(self, row, col):
        return -1

    def OnGetItemAttr(self, item):
        if item % 2 == 0:
            return self.attr1
        else:
            return self.attr2


class ChessTaskQueue(TimedTaskQueue):

    __single = None

    def __init__(self):
        if ChessTaskQueue.__single:
            raise RuntimeError, "ChessTaskQueue is singleton"
        ChessTaskQueue.__single = self
        TimedTaskQueue.__init__(self, "Chess")

    def getInstance(*args, **kw):
        if ChessTaskQueue.__single is None:
            ChessTaskQueue(*args, **kw)
        return ChessTaskQueue.__single
    getInstance = staticmethod(getInstance)
