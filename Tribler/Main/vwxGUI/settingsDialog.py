# Written by Richard Gwin
# Modified by Niels Zeilemaker
# Updated by George Milescu
# see LICENSE.txt for license information
import wx
import wx.xrc as xrc
import wx.lib.imagebrowser as ib
import sys, os
import cStringIO
import tempfile
import atexit


from Tribler.Main.vwxGUI.GuiUtility import GUIUtility
from Tribler.Main.vwxGUI.IconsManager import IconsManager, data2wxImage, data2wxBitmap, ICON_MAX_DIM
from Tribler.Main.Dialogs.socnetmyinfo import MyInfoWizard
from Tribler.Main.globals import DefaultDownloadStartupConfig,get_default_dscfg_filename
from Tribler.Main.vwxGUI.UserDownloadChoice import UserDownloadChoice
from Tribler.Core.simpledefs import DLSTATUS_SEEDING, DLSTATUS_DOWNLOADING
from Tribler.Core.API import *

class SettingsDialog(wx.Dialog):
    def __init__(self):
        self.elementsName = ['myNameField', \
                             'thumb', \
                             'edit', \
                             'browse', \
                             'firewallValue', \
                             'firewallStatusText', \
                             'firewallStatus', \
                             'familyFilter', \
                             'uploadCtrl', \
                             'downloadCtrl', \
                             'zeroUp', \
                             'fiftyUp', \
                             'hundredUp', \
                             'unlimitedUp', \
                             'seventyfiveDown', \
                             'threehundredDown', \
                             'sixhundreddDown', \
                             'unlimitedDown', \
                             'diskLocationCtrl', \
                             'diskLocationChoice', \
                             'portChange', \
                             'externalplayer',\
                             'batchstart',\
                             'batchstop']

        self.myname = None
        self.elements = {}
        self.currentPortValue = None

        pre = wx.PreDialog()
        self.PostCreate(pre)
        self.Bind(wx.EVT_WINDOW_CREATE, self.OnCreate)

    def OnCreate(self, event):
        self.Unbind(wx.EVT_WINDOW_CREATE)
        wx.CallAfter(self._PostInit)
        event.Skip()
        return True

    def _PostInit(self):
        self.guiUtility = GUIUtility.getInstance()
        self.utility = self.guiUtility.utility
        self.defaultDLConfig = DefaultDownloadStartupConfig.getInstance()

        dialog = xrc.XRCCTRL(self, "settingsDialog")
        for element in self.elementsName:
            xrcElement = xrc.XRCCTRL(dialog, element)
            if not xrcElement:
                print 'settingsOverviewPanel: Error: Could not identify xrc element:',element
            self.elements[element] = xrcElement

        #Building tree
        self.tree = xrc.XRCCTRL(self,"settings_tree")
        root = self.tree.AddRoot('Root')
        self.tree.SelectItem(self.tree.AppendItem(root,'General',data=wx.TreeItemData(xrc.XRCCTRL(self,"general_panel"))),True)
        self.tree.AppendItem(root,'Connection',data=wx.TreeItemData(xrc.XRCCTRL(self,"connection_panel")))
        self.tree.AppendItem(root,'Bandwidth',data=wx.TreeItemData(xrc.XRCCTRL(self,"bandwidth_panel")))
        self.tree.AppendItem(root,'Misc',data=wx.TreeItemData(xrc.XRCCTRL(self,"misc_panel")))
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGING, self.OnSelectionChanging)

        #Bind event listeners
        self.elements['diskLocationChoice'].Bind(wx.EVT_CHECKBOX, self.OnDownloadChoice)

        self.elements['zeroUp'].Bind(wx.EVT_BUTTON, lambda event: self.setUp(0, event))
        self.elements['fiftyUp'].Bind(wx.EVT_BUTTON, lambda event: self.setUp(50, event))
        self.elements['hundredUp'].Bind(wx.EVT_BUTTON, lambda event: self.setUp(100, event))
        self.elements['unlimitedUp'].Bind(wx.EVT_BUTTON, lambda event: self.setUp('unlimited', event))

        self.elements['seventyfiveDown'].Bind(wx.EVT_BUTTON, lambda event: self.setDown(75, event))
        self.elements['threehundredDown'].Bind(wx.EVT_BUTTON, lambda event: self.setDown(300, event))
        self.elements['sixhundreddDown'].Bind(wx.EVT_BUTTON, lambda event: self.setDown(600, event))
        self.elements['unlimitedDown'].Bind(wx.EVT_BUTTON, lambda event: self.setDown('unlimited', event))

        self.elements['uploadCtrl'].Bind(wx.EVT_KEY_DOWN, self.removeUnlimited)
        self.elements['downloadCtrl'].Bind(wx.EVT_KEY_DOWN, self.removeUnlimited)

        self.elements['edit'].Bind(wx.EVT_BUTTON, self.EditClicked)
        self.elements['browse'].Bind(wx.EVT_BUTTON, self.BrowseClicked)

        self.elements['batchstart'].Bind(wx.EVT_BUTTON, lambda event: self.OnMultiple(True))
        self.elements['batchstop'].Bind(wx.EVT_BUTTON, lambda event: self.OnMultiple(False))

        self.Bind(wx.EVT_BUTTON, self.saveAll, id = xrc.XRCID("wxID_OK"))
        self.Bind(wx.EVT_BUTTON, self.cancelAll, id = xrc.XRCID("wxID_CANCEL"))

        #Loading settings
        self.myname = self.utility.session.get_nickname()
        mime, data = self.utility.session.get_mugshot()
        if data is None:
            im = IconsManager.getInstance()
            self.mugshot = im.get_default('personsMode','DEFAULT_THUMB')
        else:
            self.mugshot = data2wxBitmap(mime, data)

        self.elements['myNameField'].SetValue(self.myname)
        self.elements['thumb'].setBitmap(self.mugshot)

        if self.guiUtility.frame.SRstatusbar.IsReachable():
            self.elements['firewallStatus'].setSelected(2)
            self.elements['firewallStatusText'].SetLabel('Port is working')

        if self.utility.config.Read('family_filter', "boolean"):
            self.elements['familyFilter'].SetSelection(0)
        else:
            self.elements['familyFilter'].SetSelection(1)

        self.currentPopup = self.guiUtility.utility.config.Read('popup_player', "boolean")
        if self.currentPopup:
            self.elements['externalplayer'].SetSelection(1)
        else:
            self.elements['externalplayer'].SetSelection(0)

        self.currentPortValue = str(self.guiUtility.get_port_number())
        self.elements['firewallValue'].SetValue(self.currentPortValue)

        maxdownloadrate = self.guiUtility.utility.config.Read('maxdownloadrate', 'int')
        if maxdownloadrate == 0:
            self.elements['downloadCtrl'].SetValue('unlimited')
        else:
            self.elements['downloadCtrl'].SetValue(str(maxdownloadrate))

        maxuploadrate = self.guiUtility.utility.config.Read('maxuploadrate', 'int')
        if maxuploadrate == -1:
            self.elements['uploadCtrl'].SetValue('0')
        elif maxuploadrate == 0:
            self.elements['uploadCtrl'].SetValue('unlimited')
        else:
            self.elements['uploadCtrl'].SetValue(str(maxuploadrate))

        self.currentDestDir = self.defaultDLConfig.get_dest_dir()
        self.elements['diskLocationCtrl'].SetValue(self.currentDestDir)
        self.elements['diskLocationCtrl'].Enable(not self.defaultDLConfig.get_show_saveas())
        self.elements['diskLocationChoice'].SetValue(self.defaultDLConfig.get_show_saveas())

        wx.CallAfter(self.Refresh)

    def OnSelectionChanging(self, event):
        old_item = event.GetOldItem()
        new_item = event.GetItem()
        try:
            self.tree.GetItemData(old_item).GetData().Hide()
            self.tree.GetItemData(new_item).GetData().Show(True)
            self.Layout()
            self.Refresh()
        except:
            pass

    def OnDownloadChoice(self, event):
        checked = self.elements['diskLocationChoice'].IsChecked()
        self.elements['diskLocationCtrl'].Enable(not checked)

    def setUp(self, value, event = None):
        self.resetUploadDownloadCtrlColour()
        self.elements['uploadCtrl'].SetValue(str(value))

        if event:
            event.Skip()

    def setDown(self, value, event = None):
        self.resetUploadDownloadCtrlColour()
        self.elements['downloadCtrl'].SetValue(str(value))

        if event:
            event.Skip()

    def resetUploadDownloadCtrlColour(self):
        self.elements['uploadCtrl'].SetForegroundColour(wx.BLACK)
        self.elements['downloadCtrl'].SetForegroundColour(wx.BLACK)

    def removeUnlimited(self, event):
        textCtrl = event.GetEventObject()
        if textCtrl.GetValue().strip() == 'unlimited':
            textCtrl.SetValue('')
        event.Skip()

    def saveAll(self, event):
        errors = {}

        valdown = self.elements['downloadCtrl'].GetValue().strip()
        if valdown != 'unlimited' and (not valdown.isdigit() or int(valdown) <= 0):
            errors['downloadCtrl'] = 'Value must be a digit'

        valup = self.elements['uploadCtrl'].GetValue().strip()
        if valup != 'unlimited' and (not valup.isdigit() or int(valup) < 0):
            errors['uploadCtrl'] = 'Value must be a digit'

        valport = self.elements['firewallValue'].GetValue().strip()
        if not valport.isdigit():
            errors['firewallValue'] = 'Value must be a digit'

        valdir = self.elements['diskLocationCtrl'].GetValue().strip()
        if not os.path.exists(valdir):
            errors['diskLocationCtrl'] = 'Location does not exist'

        valname = self.elements['myNameField'].GetValue()
        if len(valname) > 40:
            errors['myNameField'] = 'Max 40 characters'

        if len(errors) == 0: #No errors found, continue saving
            restart = False

            if valdown == 'unlimited':
                self.utility.ratelimiter.set_global_max_speed(DOWNLOAD, 0)
                self.guiUtility.utility.config.Write('maxdownloadrate', '0')
            else:
                self.utility.ratelimiter.set_global_max_speed(DOWNLOAD, int(valdown))
                self.guiUtility.utility.config.Write('maxdownloadrate', valdown)

            if valup == 'unlimited':
                self.utility.ratelimiter.set_global_max_speed(UPLOAD, 0)
                self.utility.ratelimiter.set_global_max_seedupload_speed(0)
                self.guiUtility.utility.config.Write('maxuploadrate', '0')
                self.guiUtility.utility.config.Write('maxseeduploadrate', '0')
            elif valup == '0':
                self.utility.ratelimiter.set_global_max_speed(UPLOAD, 0.0001)
                self.utility.ratelimiter.set_global_max_seedupload_speed(0.0001)
                self.guiUtility.utility.config.Write('maxuploadrate', '-1')
                self.guiUtility.utility.config.Write('maxseeduploadrate', '-1')
            else:
                self.utility.ratelimiter.set_global_max_speed(UPLOAD, int(valup))
                self.utility.ratelimiter.set_global_max_seedupload_speed(int(valup))
                self.guiUtility.utility.config.Write('maxuploadrate', valup)
                self.guiUtility.utility.config.Write('maxseeduploadrate', valup)

            if valport != self.currentPortValue:
                self.currentPortValue = self.elements['firewallValue'].GetValue()
                self.utility.config.Write('minport', valport)
                self.guiUtility.set_port_number(valport)
                self.guiUtility.set_firewall_restart(True)
                restart = True

            self.utility.config.Flush()

            showSave = self.elements['diskLocationChoice'].IsChecked()
            if showSave != self.defaultDLConfig.get_show_saveas():
                self.defaultDLConfig.set_show_saveas(showSave)
                self.saveDefaultDownloadConfig()

            if valdir != self.currentDestDir:
                self.defaultDLConfig.set_dest_dir(valdir)
                self.saveDefaultDownloadConfig()

                self.moveCollectedTorrents(self.currentDestDir, valdir)
                restart = True

            state_dir = self.utility.session.get_state_dir()
            cfgfilename = self.utility.session.get_default_config_filename(state_dir)
            scfg = SessionStartupConfig.load(cfgfilename)
            for target in [scfg,self.utility.session]:
                try:
                    target.set_nickname(self.elements['myNameField'].GetValue())
                    if getattr(self, 'icondata', False):
                        target.set_mugshot(self.icondata, mime='image/jpeg')
                except:
                    print_exc()

            scfg.save(cfgfilename)

            channelcast_db = self.utility.session.open_dbhandler(NTFY_CHANNELCAST)
            channelcast_db.updateMyChannelName(self.myname)
            self.guiUtility.toggleFamilyFilter(self.elements['familyFilter'].GetSelection() == 0)

            selectedPopup = self.elements['externalplayer'].GetSelection() == 1
            if self.currentPopup != selectedPopup:
                self.guiUtility.utility.config.Write('popup_player', selectedPopup, "boolean")
                restart = True

            if restart:
                if sys.platform == 'linux2':
                    dlg = wx.MessageDialog(self, "A restart is required for these changes to take effect.","Restart required", wx.ICON_INFORMATION|wx.OK)
                    dlg.ShowModal()
                else:
                    dlg = wx.MessageDialog(self, "A restart is required for these changes to take effect.\nDo you want to restart Tribler now?","Restart required", wx.ICON_QUESTION|wx.YES_NO|wx.YES_DEFAULT)
                    if dlg.ShowModal() == wx.ID_YES:
                        self.guiUtility.frame.Restart()
                dlg.Destroy()
            self.EndModal(1)
            event.Skip()
        else:
            for error in errors.keys():
                if sys.platform != 'darwin':
                    self.elements[error].SetForegroundColour(wx.RED)
                self.elements[error].SetValue(errors[error])

    def cancelAll(self, event):
        self.EndModal(1)

    def EditClicked(self, event = None):
        dlg = ib.ImageDialog(self, get_picture_dir())
        dlg.Centre()
        if dlg.ShowModal() == wx.ID_OK:
            self.iconpath = dlg.GetFile()
            self.process_input()
        else:
            pass
        dlg.Destroy()

    def BrowseClicked(self, event = None):
        dlg = wx.DirDialog(self,"Choose download directory", style = wx.DEFAULT_DIALOG_STYLE)
        dlg.SetPath(self.defaultDLConfig.get_dest_dir())
        if dlg.ShowModal() == wx.ID_OK:
            self.elements['diskLocationCtrl'].SetForegroundColour(wx.BLACK)
            self.elements['diskLocationCtrl'].SetValue(dlg.GetPath())
        else:
            pass

    def OnMultiple(self, start):
        user_download_choice = UserDownloadChoice.get_singleton()

        choices = []
        dstates = []
        infohashes = []

        self.guiUtility.frame.librarylist.GetManager().refresh()
        items = self.guiUtility.frame.librarylist.GetItems()
        for item in items.values():
            started = False
            ds = item.original_data.get('ds', None)
            if ds and ds.get_status() in [DLSTATUS_SEEDING, DLSTATUS_DOWNLOADING]:
                started = True

            if start != started:
                choices.append(item.original_data['name'])
                dstates.append(ds)
                infohashes.append(item.original_data["infohash"])

        if len(choices) > 0:
            message = 'Please select all torrents which should be '
            if start:
                message += 'started.'
            else:
                message += 'stopped.'
            message += "\nUse ctrl+a to select all/deselect all."

            def bindAll(control):
                control.Bind(wx.EVT_KEY_DOWN, lambda event: self._SelectAll(dlg, event, len(choices)))
                func = getattr(control, 'GetChildren', False)
                if func:
                    for child in func():
                        bindAll(child)

            dlg = wx.MultiChoiceDialog(self, message, 'Select torrents', choices)
            dlg.allselected = False
            bindAll(dlg)

            if dlg.ShowModal() == wx.ID_OK:
                selections = dlg.GetSelections()
                for selection in selections:
                    if start:
                        if dstates[selection]:
                            dstates[selection].get_download().restart()
                        user_download_choice.set_download_state(infohashes[selection], "restart")

                    else:
                        if dstates[selection]:
                            dstates[selection].get_download().stop()

                        user_download_choice.set_download_state(infohashes[selection], "stop")
        else:
            message = "No torrents in library which could be "
            if start:
                message += "started."
            else:
                message += "stopped."
            dlg = wx.MessageDialog(self, message, 'No torrents found.', wx.OK | wx.ICON_INFORMATION)
            dlg.ShowModal()
        dlg.Destroy()

    def _SelectAll(self, dlg, event, nrchoices):
        if event.ControlDown():
            if event.GetKeyCode() == 65: #ctrl + a
                if dlg.allselected:
                    dlg.SetSelections([])
                else:
                    select = list(range(nrchoices))
                    dlg.SetSelections(select)
                dlg.allselected = not dlg.allselected


    def saveDefaultDownloadConfig(self):
        # Save DownloadStartupConfig
        dlcfgfilename = get_default_dscfg_filename(self.utility.session)
        self.defaultDLConfig.save(dlcfgfilename)

        # Arno, 2010-03-08: Apparently not copied correctly from abcoptions.py
        # Save SessionStartupConfig
        # Also change torrent collecting dir, which is by default in the default destdir
        state_dir = self.utility.session.get_state_dir()
        cfgfilename = Session.get_default_config_filename(state_dir)
        scfg = SessionStartupConfig.load(cfgfilename)

        defaultdestdir = self.defaultDLConfig.get_dest_dir()
        dirname = os.path.join(defaultdestdir,STATEDIR_TORRENTCOLL_DIR)
        for target in [scfg,self.utility.session]:
            try:
                target.set_torrent_collecting_dir(dirname)
            except:
                print_exc()
        scfg.save(cfgfilename)

    def moveCollectedTorrents(self, old_dir, new_dir):
        def move(old_dir, new_dir):

            #use os.renames as much as possible
            #use single file/dir copy if target exists
            def rename_or_merge(old, new):
                if os.path.exists(old):
                    if os.path.exists(new):
                        files = os.listdir(old)
                        for file in files:
                            oldfile = os.path.join(old, file)
                            newfile = os.path.join(new, file)

                            if os.path.isdir(oldfile):
                                rename_or_merge(oldfile, newfile)
                            else:
                                os.rename(oldfile, newfile)
                    else:
                        os.renames(old, new)

            #physical move
            old_dirtf = os.path.join(old_dir, 'collected_torrent_files')
            new_dirtf = os.path.join(new_dir, 'collected_torrent_files')
            rename_or_merge(old_dirtf, new_dirtf)

            old_dirsf = os.path.join(old_dir, 'collected_subtitles_files')
            new_dirsf = os.path.join(new_dir, 'collected_subtitles_files')
            rename_or_merge(old_dirsf, new_dirsf)

            # ProxyService_
            old_dirdh = os.path.join(old_dir, 'proxyservice')
            new_dirdh = os.path.join(new_dir, 'proxyservice')
            rename_or_merge(old_dirdh, new_dirdh)

        atexit.register(move, old_dir, new_dir)

        msg = "Please wait while we update your MegaCache..."
        busyDlg = wx.BusyInfo(msg)
        try:
            time.sleep(0.3)
            wx.Yield()
        except:
            pass

        #update db
        self.guiUtility.torrentsearch_manager.torrent_db.updateTorrentDir(os.path.join(new_dir, 'collected_torrent_files'))

        busyDlg.Destroy()

    def process_input(self):
        try:
            im = wx.Image(self.iconpath)
            if im is None:
                self.show_inputerror(self.utility.lang.get('cantopenfile'))
            else:
                if sys.platform != 'darwin':
                    bm = wx.BitmapFromImage(im.Scale(ICON_MAX_DIM,ICON_MAX_DIM),-1)
                    thumbpanel = self.elements['thumb']
                    thumbpanel.setBitmap(bm)

                # Arno, 2008-10-21: scale image!
                sim = im.Scale(ICON_MAX_DIM,ICON_MAX_DIM)
                [thumbhandle,thumbfilename] = tempfile.mkstemp("user-thumb")
                os.close(thumbhandle)
                sim.SaveFile(thumbfilename,wx.BITMAP_TYPE_JPEG)

                f = open(thumbfilename,"rb")
                self.icondata = f.read()
                f.close()
                os.remove(thumbfilename)
        except:
            print_exc()
            self.show_inputerror(self.utility.lang.get('iconbadformat'))

    def show_inputerror(self,txt):
        dlg = wx.MessageDialog(self, txt, self.utility.lang.get('invalidinput'), wx.OK | wx.ICON_INFORMATION)
        dlg.ShowModal()
        dlg.Destroy()
