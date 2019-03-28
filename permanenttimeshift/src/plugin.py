#####################################################
# Permanent Timeshift Plugin for Enigma2 Dreamboxes
# Coded by Homey (c) 2013
#
# Version: 2.1
# Support: www.dreambox-plugins.de
#####################################################
from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigList, ConfigListScreen
from Components.config import config, configfile, getConfigListEntry, ConfigSubsection, ConfigYesNo, ConfigInteger, ConfigSelection, NoSave
from Components.Harddisk import harddiskmanager
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ServiceEventTracker import ServiceEventTracker
from Components.Sources.StaticText import StaticText
from Components.SystemInfo import SystemInfo
from Components.Task import Task, Job, job_manager as JobManager
from Components.UsageConfig import preferredInstantRecordPath, defaultMoviePath, defaultStorageDevice
from Screens.ChoiceBox import ChoiceBox
from Screens.ChannelSelection import ChannelSelection
from Screens.InfoBar import InfoBar as InfoBarOrg
from Screens.InfoBarGenerics import NumberZap
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.Setup import SetupSummary
from Screens.Standby import Standby, TryQuitMainloop
from Screens.PVRState import TimeshiftState
from ServiceReference import ServiceReference
from Tools import Directories, ASCIItranslit, Notifications
from Tools.BoundFunction import boundFunction
from Tools.Directories import fileExists, copyfile
from Plugins.Plugin import PluginDescriptor
from RecordTimer import RecordTimer, RecordTimerEntry, parseEvent

from random import randint
from enigma import eTimer, eServiceCenter, eBackgroundFileEraser, iPlayableService, iRecordableService, iServiceInformation
from enigma import eEPGCache
from os import environ, stat as os_stat, listdir as os_listdir, link as os_link, path as os_path, system as os_system, statvfs
from time import localtime, time, gmtime, strftime
from timer import TimerEntry

import Screens.InfoBar
import Screens.Standby

from skin import loadSkin

from Tools.Log import Log

##############################
#####  CONFIG SETTINGS   #####
##############################

VERSION = "2.1"
config.plugins.pts = ConfigSubsection()
config.plugins.pts.enabled = ConfigYesNo(default = True)
config.plugins.pts.maxevents = ConfigInteger(default=5, limits=(1, 99))
config.plugins.pts.maxlength = ConfigInteger(default=180, limits=(5, 999))
config.plugins.pts.startdelay = ConfigInteger(default=5, limits=(5, 999))
config.plugins.pts.showinfobar = ConfigYesNo(default = False)
config.plugins.pts.stopwhilerecording = ConfigYesNo(default = False)
config.plugins.pts.favoriteSaveAction = ConfigSelection([("askuser", _("Ask user")),("savetimeshift", _("Save and stop")),("savetimeshiftandrecord", _("Save and record")),("noSave", _("Don't save"))], "askuser")
config.plugins.pts.permanentrecording = ConfigYesNo(default = False)
config.plugins.pts.isRecording = NoSave(ConfigYesNo(default = False))
config.plugins.pts.showrealremainingtime = ConfigYesNo(default = True)
config.plugins.pts.showzapwarning = ConfigSelection([("no", _("No")),("simple", _("simple warning")),("advanced", _("advanced warning"))], "simple")
config.plugins.pts.zapwarning_defaultanswer1	= ConfigYesNo(default = False)
config.plugins.pts.zapwarning_defaultanswer2 = ConfigSelection([("3", _("Cancel")),("0", _("Save and stop")),("1", _("Save and record")),("2", _("Don't save"))], "3")

####################################
###  OVERWRITE CHANNELSELECTION  ###
####################################
## show a message if you want to zap in channellist on active timeshifting ##

from Screens.ChannelSelection import ChannelSelection
ChannelSelection_ori_channelSelected = ChannelSelection.channelSelected

def ChannelSelection_channelSelected(self):
	if config.plugins.pts.showzapwarning.value != "no" and config.plugins.pts.enabled.value and InfoBar.instance.timeshift_enabled and InfoBar.instance.isSeekable():
		if config.plugins.pts.showzapwarning.value == "simple":
			self.session.openWithCallback(boundFunction(ChannelSelection_channelSelected_callback1, self), MessageBox, _("You are in active timeshift-mode\n\nDo you really want to zap?"), MessageBox.TYPE_YESNO, default = config.plugins.pts.zapwarning_defaultanswer1.value)
		if config.plugins.pts.showzapwarning.value == "advanced":
			self.session.openWithCallback(boundFunction(ChannelSelection_channelSelected_callback2, self), ChoiceBox, \
				title=_("You are in active timeshift-mode\n\nWhat do you want to do on zapping?"), \
				list=((_("Save Timeshift as Movie and stop recording"), "savetimeshift"), \
				(_("Save Timeshift as Movie and continue recording"), "savetimeshiftandrecord"), \
				(_("Don't save Timeshift as Movie"), "noSave"), \
				(_("Cancel"), "noZap")), selection = int(config.plugins.pts.zapwarning_defaultanswer2.value))
	else:
		ChannelSelection_ori_channelSelected(self)

def ChannelSelection_channelSelected_callback1(self, ret):
	if ret:
		ChannelSelection_ori_channelSelected(self)

def ChannelSelection_channelSelected_callback2(self, ret):
	print "=== PTS callback", ret
	if ret is None:
			return
	if ret[1] == "noZap":
		return
	
	if ret[1] == "savetimeshift":
		InfoBar.instance.saveTimeshiftActions("savetimeshift", None)
	elif ret[1] == "savetimeshiftandrecord":
		InfoBar.instance.saveTimeshiftActions("savetimeshiftandrecord", None)
	elif ret[1] == "noSave":
		InfoBar.instance.save_current_timeshift = False
		InfoBar.instance.saveTimeshiftActions("noSave", None)
	ChannelSelection_ori_channelSelected(self)

ChannelSelection.channelSelected = ChannelSelection_channelSelected
### END OVERWRITE CHANNELSELECTION ##########

###################################
###  PTS TimeshiftSummaryScreen  ###
###################################
from Components.Sources.PTSCurrentService import PTSCurrentService

current_dialog = None

class PermanentTimeshiftSummary(Screen):
	skin = loadSkin("/usr/lib/enigma2/python/Plugins/Extensions/PermanentTimeshift/skin/skin_display.xml")

	def __init__(self, session, parent):
		Screen.__init__(self, session, parent)
		self.skinName = "PermanentTimeshiftSummary"
		self["eventname"] = Label(text="")
		self["Service"] = PTSCurrentService(session.nav, parent)

###################################
###  PTS TimeshiftState Screen  ###
###################################

from Screens.PVRState import PVRState

class InfoBarPVRState:
	def __init__(self, screen=PVRState, force_show = False, parent = None):
		self.onPlayStateChanged.append(self.__playStateChanged)
		self.pvrStateDialog = self.session.instantiateDialog(screen, parent)
		self.pvrStateDialog.neverAnimate()
		self.onShow.append(self._mayShow)
		self.onHide.append(self.pvrStateDialog.hide)
		#self.onClose.append(self.__delPvrState)
		self.force_show = force_show

#	def __delPvrState(self):
#		self.session.deleteDialog(self.pvrStateDialog)
#		self.pvrStateDialog = None

	def _mayShow(self):
		if self.execing and self.seekstate != self.SEEK_STATE_PLAY:
			self.pvrStateDialog.show()

	def __playStateChanged(self, state):
		playstateString = state[3]
		self.pvrStateDialog["state"].setText(playstateString)

		# if we return into "PLAY" state, ensure that the dialog gets hidden if there will be no infobar displayed
		# also hide if service stopped and returning into MovieList
		if not config.usage.show_infobar_on_skip.value and self.seekstate in (self.SEEK_STATE_PLAY, self.SEEK_STATE_STOP) and not self.force_show:
			self.pvrStateDialog.hide()
		else:
			self._mayShow()


class InfoBarTimeshiftState(InfoBarPVRState):
	def __init__(self, parent):
		InfoBarPVRState.__init__(self, screen=TimeshiftState, force_show = True, parent = parent)
		self.__hideTimer = eTimer()
		self.__hideTimer_conn = self.__hideTimer.timeout.connect(self.__hideTimeshiftState)

	def _mayShow(self):
		if self.execing and self.timeshift_enabled:
			self.pvrStateDialog.show()
			if self.seekstate == self.SEEK_STATE_PLAY and not self.shown:
				self.__hideTimer.start(5*1000, True)

	def __hideTimeshiftState(self):
		self.pvrStateDialog.hide()

class TimeshiftState(Screen):
	skin = loadSkin("/usr/lib/enigma2/python/Plugins/Extensions/PermanentTimeshift/skin/skin.xml")

	def __init__(self, session, parent):
		Screen.__init__(self, session, parent)
		self.skinName = "PTSStandardTimeshiftState"
		self["state"] = Label(text="")
		self["eventname"] = Label(text="")
		self["Service"] = PTSCurrentService(session.nav, parent)


class PTSTimeshiftState(Screen):
	skin = loadSkin("/usr/lib/enigma2/python/Plugins/Extensions/PermanentTimeshift/skin/skin.xml")

	def __init__(self, session):
		Screen.__init__(self, session)
		self["state"] = Label(text="")
		self["PTSSeekPointer"] = Pixmap()
		self["eventname"] = Label(text="")

###################################
###   PTS CopyTimeshift Task    ###
###################################

class CopyTimeshiftJob(Job):
	def __init__(self, toolbox, cmdline, srcfile, destfile, eventname):
		Job.__init__(self, _("Saving Timeshift files"))
		self.toolbox = toolbox
		AddCopyTimeshiftTask(self, cmdline, srcfile, destfile, eventname)

class AddCopyTimeshiftTask(Task):
	def __init__(self, job, cmdline, srcfile, destfile, eventname):
		Task.__init__(self, job, eventname)
		self.toolbox = job.toolbox
		self.setCmdline(cmdline)
		self.srcfile = config.usage.timeshift_path.value + "/" + srcfile + ".copy"
		self.destfile = destfile + ".ts"

		self.ProgressTimer = eTimer()
		self.ProgressTimer_conn = self.ProgressTimer.timeout.connect(self.ProgressUpdate)

	def ProgressUpdate(self):
		if self.srcsize <= 0 or not fileExists(self.destfile, 'r'):
			return

		self.setProgress(int((os_path.getsize(self.destfile)/float(self.srcsize))*100))
		self.ProgressTimer.start(15000, True)

	def prepare(self):
		if fileExists(self.srcfile, 'r'):
			self.srcsize = os_path.getsize(self.srcfile)
			self.ProgressTimer.start(15000, True)

		self.toolbox.ptsFrontpanelActions("start")
		config.plugins.pts.isRecording.value = True

	def afterRun(self):
		self.setProgress(100)
		self.ProgressTimer.stop()
		self.toolbox.ptsCopyFilefinished(self.srcfile, self.destfile)

###################################
###   PTS MergeTimeshift Task   ###
###################################

class MergeTimeshiftJob(Job):
	def __init__(self, toolbox, cmdline, srcfile, destfile, eventname):
		Job.__init__(self, _("Merging Timeshift files"))
		self.toolbox = toolbox
		AddMergeTimeshiftTask(self, cmdline, srcfile, destfile, eventname)

class AddMergeTimeshiftTask(Task):
	def __init__(self, job, cmdline, srcfile, destfile, eventname):
		Task.__init__(self, job, eventname)
		self.toolbox = job.toolbox
		self.setCmdline(cmdline)
		self.srcfile = config.usage.default_path.value + "/" + srcfile
		self.destfile = config.usage.default_path.value + "/" + destfile

		self.ProgressTimer = eTimer()
		self.ProgressTimer_conn = self.ProgressTimer.timeout.connect(self.ProgressUpdate)

	def ProgressUpdate(self):
		if self.srcsize <= 0 or not fileExists(self.destfile, 'r'):
			return

		self.setProgress(int((os_path.getsize(self.destfile)/float(self.srcsize))*100))
		self.ProgressTimer.start(7500, True)

	def prepare(self):
		if fileExists(self.srcfile, 'r') and fileExists(self.destfile, 'r'):
			fsize1 = os_path.getsize(self.srcfile)
			fsize2 = os_path.getsize(self.destfile)
			self.srcsize = fsize1 + fsize2
			self.ProgressTimer.start(7500, True)

		self.toolbox.ptsFrontpanelActions("start")
		config.plugins.pts.isRecording.value = True

	def afterRun(self):
		self.setProgress(100)
		self.ProgressTimer.stop()
		self.toolbox.ptsMergeFilefinished(self.srcfile, self.destfile)

##################################
###   Create APSC Files Task   ###
##################################

class CreateAPSCFilesJob(Job):
	def __init__(self, toolbox, cmdline, eventname):
		Job.__init__(self, _("Creating AP and SC Files"))
		self.toolbox = toolbox
		CreateAPSCFilesTask(self, cmdline, eventname)

class CreateAPSCFilesTask(Task):
	def __init__(self, job, cmdline, eventname):
		Task.__init__(self, job, eventname)
		self.toolbox = job.toolbox
		self.setCmdline(cmdline)

	def prepare(self):
		self.toolbox.ptsFrontpanelActions("start")
		config.plugins.pts.isRecording.value = True

	def afterRun(self):
		self.setProgress(100)
		self.toolbox.ptsSaveTimeshiftFinished()

###########################
#####  Class InfoBar  #####
###########################
class InfoBar(InfoBarOrg, InfoBarTimeshiftState):
	def __init__(self, session):
		self.pts_last_SeekState = 0
		InfoBarOrg.__init__(self, session)
		InfoBarOrg.instance = self
		InfoBarTimeshiftState.__init__(self, self)
		
		self.__event_tracker = ServiceEventTracker(screen = self, eventmap =
			{
				iPlayableService.evStart: self.__evStart,
				iPlayableService.evEnd: self.__evEnd,
				iPlayableService.evUpdatedInfo: self.__evInfoChanged,
				iPlayableService.evUpdatedEventInfo: self.__evEventInfoChanged,
				iPlayableService.evSeekableStatusChanged: self.__seekableStatusChanged,
				iPlayableService.evUser+1: self.ptsTimeshiftFileChanged
			})

		self["PTSactions"] = ActionMap(["PTS_GlobalActions"],{"instantRecord": self.instantRecord, "restartTimeshift": self.restartTimeshift},-2)
		self["PTSSeekPointerActions"] = ActionMap(["PTS_SeekPointerActions"],{"SeekPointerOK": self.ptsSeekPointerOK, "SeekPointerLeft": self.ptsSeekPointerLeft, "SeekPointerRight": self.ptsSeekPointerRight},-2)
		self["PTSSeekPointerActions"].setEnabled(False)

		self.pts_begintime = 0
		self.pts_pathchecked = False
		self.pts_pvrStateDialog = "TimeshiftState"
		self.pts_seektoprevfile = False
		self.pts_switchtolive = False
		self.pts_currplaying = 1
		self.pts_lastseekspeed = 0
		self.pts_service_changed = False
		self.pts_record_running = self.session.nav.RecordTimer.isRecording()
		self.save_current_timeshift = False
		self.save_timeshift_postaction = None
		self.save_timeshift_filename = None
		self.service_changed = 0

		# Init Global Variables
		self.session.ptsmainloopvalue = 0
		config.plugins.pts.isRecording.value = False

		# Init eBackgroundFileEraser
		self.BgFileEraser = eBackgroundFileEraser.getInstance()

		# Init PTS Delay-Timer
		self.pts_delay_timer = eTimer()
		self.pts_delay_timer_conn = self.pts_delay_timer.timeout.connect(self.activatePermanentTimeshift)

		# Init PTS LengthCheck-Timer
		self.pts_LengthCheck_timer = eTimer()
		self.pts_LengthCheck_timer_conn = self.pts_LengthCheck_timer.timeout.connect(self.ptsLengthCheck)

		# Init PTS MergeRecords-Timer
		self.pts_mergeRecords_timer = eTimer()
		self.pts_mergeRecords_timer_conn = self.pts_mergeRecords_timer.timeout.connect(self.ptsMergeRecords)

		# Init PTS Merge Cleanup-Timer
		self.pts_mergeCleanUp_timer = eTimer()
		self.pts_mergeCleanUp_timer_conn = self.pts_mergeCleanUp_timer.timeout.connect(self.ptsMergePostCleanUp)

		# Init PTS QuitMainloop-Timer
		self.pts_QuitMainloop_timer = eTimer()
		self.pts_QuitMainloop_timer_conn = self.pts_QuitMainloop_timer.timeout.connect(self.ptsTryQuitMainloop)

		# Init PTS CleanUp-Timer
		self.pts_cleanUp_timer = eTimer()
		self.pts_cleanUp_timer_conn = self.pts_cleanUp_timer.timeout.connect(self.ptsCleanTimeshiftFolder)
		self.pts_cleanUp_timer.start(30000, True)

		# Init PTS SeekBack-Timer
		self.pts_SeekBack_timer = eTimer()
		self.pts_SeekBack_timer_conn = self.pts_SeekBack_timer.timeout.connect(self.ptsSeekBackTimer)

		# Init Block-Zap Timer
		self.pts_blockZap_timer = eTimer()

		# Record Event Tracker
		self.session.nav.RecordTimer.on_state_change.append(self.ptsTimerEntryStateChange)

		# Keep Current Event Info for recordings
		self.pts_eventcount = 1
		self.pts_curevent_begin = int(time())
		self.pts_curevent_end = 0
		self.pts_curevent_name = _("Timeshift")
		self.pts_curevent_description = ""
		self.pts_curevent_servicerefname = ""
		self.pts_curevent_station = ""
		self.pts_curevent_eventid = None
		self.pts_list = dict() # save the eventinfos of the ptsfiles

		# set summary-variables
		self.pts_summary_org = None
		self.pts_summary = None
		self.pts_InfoBar_org = None
		
		# Init PTS Infobar
		self.pts_seekpointer_MinX = 8
		self.pts_seekpointer_MaxX = 396 # make sure you can divide this through 2
		
		# set __restoreState for setSummaryScreen
		self.onShown.append(self.__restoreState)

	def __evStart(self):
		self.service_changed = 1
		self.pts_delay_timer.stop()
		self.pts_service_changed = True

	def __evEnd(self):
		self.service_changed = 0

	def __evInfoChanged(self):
		if self.service_changed:
			self.service_changed = 0

			# We zapped away before saving the file, save it now!
			if self.save_current_timeshift:
				self.SaveTimeshift("pts_livebuffer.%s" % (self.pts_eventcount))

			# Delete Timeshift Records on zap
			self.pts_eventcount = 0
			self.pts_cleanUp_timer.start(3000, True)

	def __evEventInfoChanged(self):

		if not config.plugins.pts.enabled.value:
			return

		# Get Current Event Info
		service = self.session.nav.getCurrentService()
		old_begin_time = self.pts_begintime
		info = service and service.info()
		ptr = info and info.getEvent(0)
		self.pts_begintime = ptr and ptr.getBeginTime() or 0

		# Save current TimeShift permanently now ...
		if info.getInfo(iServiceInformation.sVideoPID) != -1:

			# Take care of Record Margin Time ...
			if self.save_current_timeshift and self.timeshift_enabled:
				if config.recording.margin_after.value > 0 and len(self.recording) == 0:
					self.SaveTimeshift(mergelater=True)
					recording = RecordTimerEntry(ServiceReference(self.session.nav.getCurrentlyPlayingServiceReference()), time(), time()+(config.recording.margin_after.value*60), self.pts_curevent_name, self.pts_curevent_description, self.pts_curevent_eventid, dirname = config.usage.default_path.value)
					recording.dontSave = True
					self.session.nav.RecordTimer.record(recording)
					self.recording.append(recording)
				else:
					self.SaveTimeshift()

			# Restarting active timers after zap ...
			if self.pts_delay_timer.isActive() and not self.timeshift_enabled:
				self.pts_delay_timer.start(config.plugins.pts.startdelay.value*1000, True)
			if self.pts_cleanUp_timer.isActive() and not self.timeshift_enabled:
				self.pts_cleanUp_timer.start(3000, True)

			# (Re)Start TimeShift
			if not self.pts_delay_timer.isActive():
				if not self.timeshift_enabled or old_begin_time != self.pts_begintime or old_begin_time == 0:
					if self.pts_service_changed:
						self.pts_service_changed = False
						self.pts_delay_timer.start(config.plugins.pts.startdelay.value*1000, True)
					else:
						self.pts_delay_timer.start(1000, True)

	def __seekableStatusChanged(self):
		enabled = False
		if not self.isSeekable() and self.timeshift_enabled:
			enabled = True
		self["TimeshiftActivateActions"].setEnabled(enabled)

		enabled = False
		if config.plugins.pts.enabled.value and config.plugins.pts.showinfobar.value and self.timeshift_enabled and self.isSeekable():
			enabled = True

		self["PTSSeekPointerActions"].setEnabled(enabled)
		
		# set Display to InfoBarSummary if timeshift is ended
		if InfoBar and InfoBar.instance and self.execing and config.plugins.pts.enabled.value and self.timeshift_enabled and not self.isSeekable():
			self.setSummary(False)

		# Reset Seek Pointer And Eventname in InfoBar
		if config.plugins.pts.enabled.value and config.plugins.pts.showinfobar.value and self.timeshift_enabled and not self.isSeekable():
			if self.pts_pvrStateDialog == "PTSTimeshiftState":
				self.pvrStateDialog["eventname"].setText("")
			self.ptsSeekPointerReset()

		# setNextPlaybackFile() when switching back to live tv
		if config.plugins.pts.enabled.value and self.timeshift_enabled and not self.isSeekable():
			if self.pts_starttime <= (time()-5):
				self.pts_blockZap_timer.start(3000, True)
			self.pts_currplaying = self.pts_eventcount
			self.ptsSetNextPlaybackFile("pts_livebuffer.%s" % (self.pts_eventcount))

	def activatePermanentTimeshift(self):
		if self.ptsCheckTimeshiftPath() is False or self.session.screen["Standby"].boolean is True or self.ptsLiveTVStatus() is False or (config.plugins.pts.stopwhilerecording.value and self.pts_record_running):
			return

		# Set next-file on event change only when watching latest timeshift ...
		if self.isSeekable() and self.pts_eventcount == self.pts_currplaying:
			pts_setnextfile = True
		else:
			pts_setnextfile = False

		# Update internal Event Counter
		if self.pts_eventcount >= config.plugins.pts.maxevents.value:
			self.pts_eventcount = 0

		self.pts_eventcount += 1

		# Do not switch back to LiveTV while timeshifting
		if self.isSeekable():
			switchToLive = False
		else:
			switchToLive = True

		# setNextPlaybackFile() on event change while timeshifting
		if self.pts_eventcount > 1 and self.isSeekable() and pts_setnextfile:
			self.ptsSetNextPlaybackFile("pts_livebuffer.%s" % (self.pts_eventcount))

		# (Re)start Timeshift now
		self.stopTimeshiftConfirmed(True, switchToLive)
		ts = self.getTimeshift()
		if ts and not ts.startTimeshift():
			self.pts_starttime = time()
			self.pts_LengthCheck_timer.start(120000)
			self.timeshift_enabled = 1
			self.save_timeshift_postaction = None
			self.ptsGetEventInfo()
			self.ptsCreateHardlink()
			self.__seekableStatusChanged()
		else:
			self.pts_eventcount = 0

	# set the correct pvrStateDialog 
	def setPVRStateDialog(self):
		if config.plugins.pts.showinfobar.value and self.pts_pvrStateDialog != "PTSTimeshiftState":
			#self.pvrStateDialog.hide()
			if self.pts_InfoBar_org is None:
				self.pts_InfoBar_org = self.pvrStateDialog
			self.pts_pvrStateDialog = "PTSTimeshiftState"
			self.pvrStateDialog = self.session.instantiateDialog(PTSTimeshiftState)
			#self.pvrStateDialog.show()
		elif not config.plugins.pts.showinfobar.value and self.pts_pvrStateDialog != "TimeshiftState":
			#self.pvrStateDialog.hide()
			self.pts_pvrStateDialog = "TimeshiftState"
			if self.pts_InfoBar_org is not None:
				self.pvrStateDialog = self.pts_InfoBar_org
			else:
				self.pvrStateDialog = self.session.instantiateDialog(TimeshiftState, self)
			#self.pvrStateDialog.show()
	
	# activates timeshift, and seeks to (almost) the end
	def activateTimeshiftEnd(self, back = True):
		self.setPVRStateDialog()
		InfoBarOrg.activateTimeshiftEnd(self, back)
		#self.setPVRStateDialog()
		self.setSummary(True)
		#set eventname in PTS Infobar and Display if pts is enabled
		if config.plugins.pts.enabled.value:
			self.pvrStateDialog["eventname"].setText(self.pts_curevent_name)
			self.session.summary["eventname"].setText(self.pts_curevent_name)
		

	def startTimeshift(self):
		if config.plugins.pts.enabled.value:
			self.pts_delay_timer.stop()
			self.activatePermanentTimeshift()
			self.activateTimeshiftEndAndPause()
		else:
			InfoBarOrg.startTimeshift(self)

	def setSummary(self, setPTSSummary = False):
		if not config.plugins.pts.enabled.value:
			return
		self.session.summary.hide()
		if setPTSSummary:
			if self.pts_summary_org is None:
				self.pts_summary_org = self.session.summary
			if self.pts_summary is None:
				self.pts_summary = self.session.instantiateSummaryDialog(PermanentTimeshiftSummary, self.session.current_dialog)
				self.session.current_dialog.addSummary(self.pts_summary) #to prevent crash at summaries.remove in GUISkin
			self.session.summary = self.pts_summary
		else:
			if self.pts_summary_org and self.session.summary != self.pts_summary_org:
				self.session.summary = self.pts_summary_org
		self.session.summary.show()

	def __hideState_onExecEnd(self):
		self.pvrStateDialog.hide() # hide pts Infobar on open other screens
	
	def __restoreState(self):
		if self.pts_summary_org is None:
			return
		if config.plugins.pts.enabled.value:
			if self.isSeekable():
				self.setSummary(True)
			else:
				self.setSummary(False)

	##############################################################################
	## Skin positions-functions
	def getLength(self):
		try:
			if not config.plugins.pts.showrealremainingtime.value:
				return self.ptsGetLength()
		except:
			pass
		
		try:
			if self.pts_eventcount != self.pts_currplaying:
				before_pts = self.pts_list[str(self.pts_currplaying)]['before_pts']*90000
				return int(before_pts) + self.ptsGetLength()
			else:
				duration = int( self.pts_list[str(self.pts_currplaying)]['end'] - self.pts_list[str(self.pts_currplaying)]['begin']) - (int(config.recording.margin_after.value * 60) + int(config.recording.margin_before.value * 60) )
				return int(duration) * 90000
			
		except:
			# Fallback
			return self.ptsGetLength()

	def getLivePosition(self): #get Position of Live-TV-Event
		try:
			#if not current live-event, give back no LivePosition
			if not config.plugins.pts.showrealremainingtime.value or self.pts_eventcount != self.pts_currplaying:
				return 0
		except:
			pass
		
		try:
			begin_time = int(self.pts_curevent_begin + int(config.recording.margin_before.value * 60) )
			live_position = int(time() - begin_time)*90000
			return live_position
		except:
			return self.ptsGetLength()
		
	def getPTSPosition(self): #get Position of PTS include the time before pts
		
		try:
			if not config.plugins.pts.showrealremainingtime.value: 
				return self.ptsGetPosition()
		except:
			pass
		
		try:
			before_pts = self.pts_list[str(self.pts_currplaying)]['before_pts']*90000
			return int(before_pts) + self.ptsGetPosition()
		except:
			return self.ptsGetPosition()

	def getBeforePTSPosition(self): #get Position before PTS startet
		try:
			if not config.plugins.pts.showrealremainingtime.value:
				# get begin from currplaying-file
				return 0
		except:
			pass
		
		try:
			before_pts = self.pts_list[str(self.pts_currplaying)]['before_pts']*90000
			return int(before_pts)
		except:
			return 0

	##############################################################################

	def stopTimeshift(self):
		if not self.timeshift_enabled:
			return 0

		# Jump Back to Live TV
		if config.plugins.pts.enabled.value and self.timeshift_enabled:
			if self.isSeekable():
				self.pts_switchtolive = True
				self.ptsSetNextPlaybackFile(None)
				if self.seekstate != self.SEEK_STATE_PLAY:
					self.setSeekState(self.SEEK_STATE_PLAY)
				self.setSeekState(self.SEEK_STATE_STOP)
				self.doSeek(3600 * 24 * 90000)
				self.doSeek(3600 * 24 * 90000) #make second if sometimes first not work
				self.setSummary(False)
				return 1
			self.setSummary(False)
			return 0
		InfoBarOrg.stopTimeshift(self)
		self.setSummary(False)


	def stopTimeshiftConfirmed(self, confirmed, switchToLive=True):
		was_enabled = self.timeshift_enabled

		if not confirmed:
			return
		ts = self.getTimeshift()
		if ts is None:
			return

		# Stop Timeshift now
		try:
			ts.stopTimeshift(switchToLive)
		except:
			ts.stopTimeshift()

		self.timeshift_enabled = 0
		self.__seekableStatusChanged()

		if was_enabled and not self.timeshift_enabled:
			self.timeshift_enabled = 0
			self.pts_LengthCheck_timer.stop()

	def restartTimeshift(self):
		self.activatePermanentTimeshift()
		Notifications.AddNotification(MessageBox, _("[PTS-Plugin] Restarting Timeshift!"), MessageBox.TYPE_INFO, timeout=5)

	def saveTimeshiftPopup(self):
		self.session.openWithCallback(self.saveTimeshiftPopupCallback, ChoiceBox, \
			title=_("The Timeshift record was not saved yet!\nWhat do you want to do now with the timeshift file?"), \
			list=((_("Save Timeshift as Movie and stop recording"), "savetimeshift"), \
			(_("Save Timeshift as Movie and continue recording"), "savetimeshiftandrecord"), \
			(_("Don't save Timeshift as Movie"), "noSave")))

	def saveTimeshiftPopupCallback(self, answer):
		if answer is None:
			return

		if answer[1] == "savetimeshift":
			self.saveTimeshiftActions("savetimeshift", self.save_timeshift_postaction)
		elif answer[1] == "savetimeshiftandrecord":
			self.saveTimeshiftActions("savetimeshiftandrecord", self.save_timeshift_postaction)
		elif answer[1] == "noSave":
			self.save_current_timeshift = False
			self.saveTimeshiftActions("noSave", self.save_timeshift_postaction)

	def saveTimeshiftEventPopup(self):
		filecount = 0
		entrylist = []
		entrylist.append((_("Current Event:")+" %s" % (self.pts_curevent_name), "savetimeshift"))

		filelist = os_listdir(config.usage.timeshift_path.value)

		if filelist is not None:
			filelist.sort()

		for filename in filelist:
			if (filename.startswith("pts_livebuffer.") is True) and (filename.endswith(".del") is False and filename.endswith(".meta") is False and filename.endswith(".eit") is False and filename.endswith(".copy") is False):
				statinfo = os_stat("%s/%s" % (config.usage.timeshift_path.value,filename))
				if statinfo.st_mtime < (time()-5.0):
					# Get Event Info from meta file
					readmetafile = open("%s/%s.meta" % (config.usage.timeshift_path.value,filename), "r")
					servicerefname = readmetafile.readline()[0:-1]
					eventname = readmetafile.readline()[0:-1]
					description = readmetafile.readline()[0:-1]
					begintime = readmetafile.readline()[0:-1]
					readmetafile.close()

					# Add Event to list
					filecount += 1
					entrylist.append((_("Record") + " #%s (%s): %s" % (filecount,strftime("%H:%M",localtime(int(begintime))),eventname), "%s" % filename))

		self.session.openWithCallback(self.recordQuestionCallback, ChoiceBox, title=_("Which event do you want to save permanently?"), list=entrylist)

	def saveTimeshiftActions(self, action=None, postaction=None):
		self.save_timeshift_postaction = postaction

		if action is None:
			if config.plugins.pts.favoriteSaveAction.value == "askuser":
				self.saveTimeshiftPopup()
				return
			elif config.plugins.pts.favoriteSaveAction.value == "savetimeshift":
				self.SaveTimeshift()
			elif config.plugins.pts.favoriteSaveAction.value == "savetimeshiftandrecord":
				if self.pts_curevent_end > time():
					self.SaveTimeshift(mergelater=True)
					self.ptsRecordCurrentEvent()
				else:
					self.SaveTimeshift()
			elif config.plugins.pts.favoriteSaveAction.value == "noSave":
				config.plugins.pts.isRecording.value = False
				self.save_current_timeshift = False
		elif action == "savetimeshift":
			self.SaveTimeshift()
		elif action == "savetimeshiftandrecord":
			if self.pts_curevent_end > time():
				self.SaveTimeshift(mergelater=True)
				self.ptsRecordCurrentEvent()
			else:
				self.SaveTimeshift()
		elif action == "noSave":
			config.plugins.pts.isRecording.value = False
			self.save_current_timeshift = False

		# Workaround: Show Dummy Popup for a second to prevent StandBy Bug
		if action is None and postaction == "standby" and (config.plugins.pts.favoriteSaveAction.value == "savetimeshift" or config.plugins.pts.favoriteSaveAction.value == "savetimeshiftandrecord"):
			self.session.open(MessageBox, _("Saving timeshift as movie now. This might take a while!"), MessageBox.TYPE_INFO, timeout=1)
			
		# Post PTS Actions like ZAP or whatever the user requested
		if self.save_timeshift_postaction == "zapUp":
			InfoBarOrg.zapUp(self)
		elif self.save_timeshift_postaction == "zapDown":
			InfoBarOrg.zapDown(self)
		elif self.save_timeshift_postaction == "historyBack":
			InfoBarOrg.historyBack(self)
		elif self.save_timeshift_postaction == "historyNext":
			InfoBarOrg.historyNext(self)
		elif self.save_timeshift_postaction == "switchChannelUp":
			InfoBarOrg.switchChannelUp(self)
		elif self.save_timeshift_postaction == "switchChannelDown":
			InfoBarOrg.switchChannelDown(self)
		elif self.save_timeshift_postaction == "openServiceList":
			InfoBarOrg.openServiceList(self)
		elif self.save_timeshift_postaction == "showRadioChannelList":
			InfoBarOrg.showRadioChannelList(self, zap=True)
		elif self.save_timeshift_postaction == "standby":
			Notifications.AddNotification(Screens_Standby_Standby)

	def SaveTimeshift(self, timeshiftfile=None, mergelater=False):
		self.save_current_timeshift = False
		savefilename = None

		if timeshiftfile is not None:
			savefilename = timeshiftfile

		if savefilename is None:
			for filename in os_listdir(config.usage.timeshift_path.value):
				if filename.startswith("timeshift.") and not filename.endswith(".del") and not filename.endswith(".copy") and not filename.endswith(".sc"):
					try:
						statinfo = os_stat("%s/%s" % (config.usage.timeshift_path.value,filename))
						if statinfo.st_mtime > (time()-5.0):
							savefilename=filename
					except Exception, errormsg:
						Notifications.AddNotification(MessageBox, _("PTS Plugin Error: %s" % (errormsg)), MessageBox.TYPE_ERROR)

		if savefilename is None:
			Notifications.AddNotification(MessageBox, _("No Timeshift found to save as recording!"), MessageBox.TYPE_ERROR)
		else:
			timeshift_saved = True
			timeshift_saveerror1 = ""
			timeshift_saveerror2 = ""
			metamergestring = ""

			config.plugins.pts.isRecording.value = True

			if mergelater:
				self.pts_mergeRecords_timer.start(120000, True)
				metamergestring = "pts_merge\n"

			try:
				if timeshiftfile is None:
					# Save Current Event by creating hardlink to ts file
					if self.pts_starttime >= (time()-60):
						self.pts_starttime -= 60

					ptsfilename = "%s - %s - %s" % (strftime("%Y%m%d %H%M",localtime(self.pts_starttime)),self.pts_curevent_station,self.pts_curevent_name)
					try:
						if config.usage.setup_level.index >= 2:
							if config.recording.filename_composition.value == "long" and self.pts_curevent_name != pts_curevent_description:
								ptsfilename = "%s - %s - %s - %s" % (strftime("%Y%m%d %H%M",localtime(self.pts_starttime)),self.pts_curevent_station,self.pts_curevent_name,self.pts_curevent_description)
							elif config.recording.filename_composition.value == "short":
								ptsfilename = "%s - %s" % (strftime("%Y%m%d",localtime(self.pts_starttime)),self.pts_curevent_name)
					except Exception, errormsg:
						Log.i("[PTS-Plugin] Using default filename")

					if config.recording.ascii_filenames.value:
						ptsfilename = ASCIItranslit.legacyEncode(ptsfilename)

					fullname = Directories.getRecordingFilename(ptsfilename,config.usage.default_path.value)
					os_link("%s/%s" % (config.usage.timeshift_path.value,savefilename), "%s.ts" % (fullname))
					metafile = open("%s.ts.meta" % (fullname), "w")
					metafile.write("%s\n%s\n%s\n%i\n%s" % (self.pts_curevent_servicerefname,self.pts_curevent_name.replace("\n", ""),self.pts_curevent_description.replace("\n", ""),int(self.pts_starttime),metamergestring))
					metafile.close()
					self.ptsCreateEITFile(fullname)
				elif timeshiftfile.startswith("pts_livebuffer"):
					# Save stored timeshift by creating hardlink to ts file
					readmetafile = open("%s/%s.meta" % (config.usage.timeshift_path.value,timeshiftfile), "r")
					servicerefname = readmetafile.readline()[0:-1]
					eventname = readmetafile.readline()[0:-1]
					description = readmetafile.readline()[0:-1]
					begintime = readmetafile.readline()[0:-1]
					readmetafile.close()

					ptsfilename = "%s - %s - %s" % (strftime("%Y%m%d %H%M",localtime(int(begintime))),self.pts_curevent_station,eventname)
					try:
						if config.usage.setup_level.index >= 2:
							if config.recording.filename_composition.value == "long" and eventname != description:
								ptsfilename = "%s - %s - %s - %s" % (strftime("%Y%m%d %H%M",localtime(int(begintime))),self.pts_curevent_station,eventname,description)
							elif config.recording.filename_composition.value == "short":
								ptsfilename = "%s - %s" % (strftime("%Y%m%d",localtime(int(begintime))),eventname)
					except Exception, errormsg:
						Log.i("[PTS-Plugin] Using default filename")

					if config.recording.ascii_filenames.value:
						ptsfilename = ASCIItranslit.legacyEncode(ptsfilename)

					fullname=Directories.getRecordingFilename(ptsfilename,config.usage.default_path.value)
					os_link("%s/%s" % (config.usage.timeshift_path.value,timeshiftfile),"%s.ts" % (fullname))
					os_link("%s/%s.meta" % (config.usage.timeshift_path.value,timeshiftfile),"%s.ts.meta" % (fullname))
					if fileExists("%s/%s.eit" % (config.usage.timeshift_path.value,timeshiftfile)):
						os_link("%s/%s.eit" % (config.usage.timeshift_path.value,timeshiftfile),"%s.eit" % (fullname))

					# Add merge-tag to metafile
					if mergelater:
						metafile = open("%s.ts.meta" % (fullname), "a")
						metafile.write("%s\n" % (metamergestring))
						metafile.close()

				# Create AP and SC Files when not merging
				if not mergelater:
					self.ptsCreateAPSCFiles(fullname+".ts")

			except Exception, errormsg:
				timeshift_saved = False
				timeshift_saveerror1 = errormsg

			# Hmpppf! Saving Timeshift via Hardlink-Method failed. Probably other device?
			# Let's try to copy the file in background now! This might take a while ...
			if not timeshift_saved:
				try:
					stat = statvfs(config.usage.default_path.value)
					freespace = stat.f_bfree / 1000 * stat.f_bsize / 1000
					randomint = randint(1, 999)

					if timeshiftfile is None:
						# Get Filesize for Free Space Check
						filesize = int(os_path.getsize("%s/%s" % (config.usage.timeshift_path.value,savefilename)) / (1024*1024))

						# Save Current Event by copying it to the other device
						if filesize <= freespace:
							os_link("%s/%s" % (config.usage.timeshift_path.value,savefilename), "%s/%s.%s.copy" % (config.usage.timeshift_path.value,savefilename,randomint))
							copy_file = savefilename
							metafile = open("%s.ts.meta" % (fullname), "w")
							metafile.write("%s\n%s\n%s\n%i\n%s" % (self.pts_curevent_servicerefname,self.pts_curevent_name.replace("\n", ""),self.pts_curevent_description.replace("\n", ""),int(self.pts_starttime),metamergestring))
							metafile.close()
							self.ptsCreateEITFile(fullname)
					elif timeshiftfile.startswith("pts_livebuffer"):
						# Get Filesize for Free Space Check
						filesize = int(os_path.getsize("%s/%s" % (config.usage.timeshift_path.value, timeshiftfile)) / (1024*1024))

						# Save stored timeshift by copying it to the other device
						if filesize <= freespace:
							os_link("%s/%s" % (config.usage.timeshift_path.value,timeshiftfile), "%s/%s.%s.copy" % (config.usage.timeshift_path.value,timeshiftfile,randomint))
							copyfile("%s/%s.meta" % (config.usage.timeshift_path.value,timeshiftfile),"%s.ts.meta" % (fullname))
							if fileExists("%s/%s.eit" % (config.usage.timeshift_path.value,timeshiftfile)):
								copyfile("%s/%s.eit" % (config.usage.timeshift_path.value,timeshiftfile),"%s.eit" % (fullname))
							copy_file = timeshiftfile

						# Add merge-tag to metafile
						if mergelater:
							metafile = open("%s.ts.meta" % (fullname), "a")
							metafile.write("%s\n" % (metamergestring))
							metafile.close()

					# Only copy file when enough disk-space available!
					if filesize <= freespace:
						timeshift_saved = True
						copy_file = copy_file+"."+str(randomint)

						# Get Event Info from meta file
						if fileExists("%s.ts.meta" % (fullname)):
							readmetafile = open("%s.ts.meta" % (fullname), "r")
							servicerefname = readmetafile.readline()[0:-1]
							eventname = readmetafile.readline()[0:-1]
						else:
							eventname = "";

						JobManager.AddJob(CopyTimeshiftJob(self, "cp \"%s/%s.copy\" \"%s.ts\"" % (config.usage.timeshift_path.value,copy_file,fullname), copy_file, fullname, eventname))
						if not Screens.Standby.inTryQuitMainloop and not Screens.Standby.inStandby and not mergelater and self.save_timeshift_postaction != "standby":
							Notifications.AddNotification(MessageBox, _("Saving timeshift as movie now. This might take a while!"), MessageBox.TYPE_INFO, timeout=5)
					else:
						timeshift_saved = False
						timeshift_saveerror1 = ""
						timeshift_saveerror2 = _("Not enough free Diskspace!\n\nFilesize: %sMB\nFree Space: %sMB\nPath: %s" % (filesize,freespace,config.usage.default_path.value))

				except Exception, errormsg:
					timeshift_saved = False
					timeshift_saveerror2 = errormsg

			if not timeshift_saved:
				config.plugins.pts.isRecording.value = False
				self.save_timeshift_postaction = None
				errormessage = str(timeshift_saveerror1) + "\n" + str(timeshift_saveerror2)
				Notifications.AddNotification(MessageBox, _("Timeshift save failed!")+"\n\n%s" % errormessage, MessageBox.TYPE_ERROR)

	def zapUp(self):
		if self.pts_blockZap_timer.isActive():
			return

		if self.save_current_timeshift and self.timeshift_enabled:
			self.saveTimeshiftActions(postaction="zapUp")
		else:
			InfoBarOrg.zapUp(self)

	def zapDown(self):
		if self.pts_blockZap_timer.isActive():
			return

		if self.save_current_timeshift and self.timeshift_enabled:
			self.saveTimeshiftActions(postaction="zapDown")
		else:
			InfoBarOrg.zapDown(self)

	def historyBack(self):
		if self.pts_pvrStateDialog == "PTSTimeshiftState" and self.timeshift_enabled and self.isSeekable():
			InfoBarOrg._mayShow(self)
			self.pvrStateDialog["PTSSeekPointer"].setPosition(self.pts_seekpointer_MinX, self.pvrStateDialog["PTSSeekPointer"].position[1])
			if self.seekstate != self.SEEK_STATE_PLAY:
				self.setSeekState(self.SEEK_STATE_PLAY)
			self.ptsSeekPointerOK()
		elif self.save_current_timeshift and self.timeshift_enabled:
			self.saveTimeshiftActions(postaction="historyBack")
		elif self.timeshift_enabled and self.isSeekable():
			#return #skip if timeshifting
			self.doSeekRelative(-self.ptsGetPosition())
			#self.doSeek(0)
		else:
			InfoBarOrg.historyBack(self)

	def historyNext(self):
		if self.pts_pvrStateDialog == "PTSTimeshiftState" and self.timeshift_enabled and self.isSeekable():
			InfoBarOrg._mayShow(self)
			self.pvrStateDialog["PTSSeekPointer"].setPosition(self.pts_seekpointer_MaxX, self.pvrStateDialog["PTSSeekPointer"].position[1])
			if self.seekstate != self.SEEK_STATE_PLAY:
				self.setSeekState(self.SEEK_STATE_PLAY)
			self.ptsSeekPointerOK()
		elif self.save_current_timeshift and self.timeshift_enabled:
			self.saveTimeshiftActions(postaction="historyNext")
		elif self.timeshift_enabled and self.isSeekable():
			#return #skip if timeshifting
			self.doSeekRelative(self.ptsGetLength()-self.ptsGetPosition())
		else:
			InfoBarOrg.historyNext(self)

	def switchChannelUp(self):
		if self.save_current_timeshift and self.timeshift_enabled and config.plugins.pts.showzapwarning.value != "advanced":
			self.saveTimeshiftActions(postaction="switchChannelUp")
		else:
			InfoBarOrg.switchChannelUp(self)

	def switchChannelDown(self):
		if self.save_current_timeshift and self.timeshift_enabled and config.plugins.pts.showzapwarning.value != "advanced":
			self.saveTimeshiftActions(postaction="switchChannelDown")
		else:
			InfoBarOrg.switchChannelDown(self)

	def openServiceList(self):
		if self.save_current_timeshift and self.timeshift_enabled:
			self.saveTimeshiftActions(postaction="openServiceList")
		else:
			InfoBarOrg.openServiceList(self)

	def showRadioChannelList(self, zap=False):
		if self.save_current_timeshift and self.timeshift_enabled:
			self.saveTimeshiftActions(postaction="showRadioChannelList")
		else:
			InfoBarOrg.showRadioChannelList(self, zap)

	def keyNumberGlobal(self, number):
		if self.pts_pvrStateDialog == "PTSTimeshiftState" and self.timeshift_enabled and self.isSeekable() and number == 0:
			InfoBarOrg._mayShow(self)
			self.pvrStateDialog["PTSSeekPointer"].setPosition(self.pts_seekpointer_MaxX/2, self.pvrStateDialog["PTSSeekPointer"].position[1])
			if self.seekstate != self.SEEK_STATE_PLAY:
				self.setSeekState(self.SEEK_STATE_PLAY)
			self.ptsSeekPointerOK()
			return
		if self.timeshift_enabled and self.isSeekable() and (number == 0 or number == 2 or number == 5 or number == 8):
			return # ingnore key wile timeshifting

		if self.pts_blockZap_timer.isActive():
			return

		if self.save_current_timeshift and self.timeshift_enabled:
			self.saveTimeshiftActions()
			return

		InfoBarOrg.keyNumberGlobal(self, number)
		if number and config.plugins.pts.enabled.value and self.timeshift_enabled and not self.isSeekable():
			self.session.openWithCallback(self.numberEntered, NumberZap, number)

	def _mayShow(self):
		if InfoBar and InfoBar.instance and self.execing and self.timeshift_enabled and self.isSeekable():
			self.ptsSeekPointerSetCurrentPos()
			self.pvrStateDialog.show()
			
			self.pvrstate_hide_timer = eTimer()
			self.pvrstate_hide_timer_conn = self.pvrstate_hide_timer.timeout.connect(self.pvrStateDialog.hide)
			self.pvrstate_hide_timer.stop()

			if self.seekstate == self.SEEK_STATE_PLAY:
				idx = config.usage.infobar_timeout.index
				if not idx:
					idx = 5
				self.pvrstate_hide_timer.start(idx*1000, True)
			else:
				self.pvrstate_hide_timer.stop()

		elif self.execing and self.timeshift_enabled and not self.isSeekable():
			# show ptsInfoBar for 1 Seconds after Stop Timeshift
			if self.pts_last_SeekState == self.SEEK_STATE_STOP:
				self.pvrStateDialog["state"].setText(self.pts_last_SeekState[3])
				self.pvrstate_hide_timer = eTimer()
				self.pvrstate_hide_timer_conn = self.pvrstate_hide_timer.timeout.connect(self.pvrStateDialog.hide)
				self.pvrstate_hide_timer.start(1*1000, True)
			else:
				self.pvrStateDialog.hide()
		else:
			InfoBarOrg._mayShow(self)

	def seekBack(self):
		InfoBarOrg.seekBack(self)
		self.pts_lastseekspeed = self.seekstate[1]

	def setSeekState(self, state, onlyGUI = False):
		# SEEK_STATE_PLAY = (0, 0, 0, ">")
		# SEEK_STATE_PAUSE = (1, 0, 0, "||")
		# SEEK_STATE_EOF = (1, 0, 0, "END")
		# SEEK_STATE_STOP = (0, 0, 0, "STOP")
		InfoBarOrg.setSeekState(self, state, onlyGUI)
		if not config.plugins.pts.enabled.value or not self.timeshift_enabled:
			return
		self.pts_last_SeekState = state
		self.ptsHandleSeekBackward()

	def doSeekRelative(self, pts):
		InfoBarOrg.doSeekRelative(self, pts)
		if config.plugins.pts.enabled.value and config.usage.show_infobar_on_skip.value:
			self.showAfterSeek()

	def instantRecord(self):
		if not config.plugins.pts.enabled.value or not self.timeshift_enabled:
			InfoBarOrg.instantRecord(self)
			return

		dir = preferredInstantRecordPath()
		if not dir or not fileExists(dir, 'w'):
			dir = defaultMoviePath()

		if not harddiskmanager.inside_mountpoint(dir):
			if harddiskmanager.HDDCount() and not harddiskmanager.HDDEnabledCount():
				self.session.open(MessageBox, _("Unconfigured storage devices found!") + "\n" \
					+ _("Please make sure to set up your storage devices with the storage management in menu -> setup -> system -> storage devices."), MessageBox.TYPE_ERROR)
				return
			elif harddiskmanager.HDDEnabledCount() and defaultStorageDevice() == "<undefined>":
				self.session.open(MessageBox, _("No default storage device found!") + "\n" \
					+ _("Please make sure to set up your default storage device in menu -> setup -> system -> recording paths."), MessageBox.TYPE_ERROR)
				return
			elif harddiskmanager.HDDEnabledCount() and defaultStorageDevice() != "<undefined>":
				part = harddiskmanager.getDefaultStorageDevicebyUUID(defaultStorageDevice())
				if part is None:
					self.session.open(MessageBox, _("Default storage device is not available!") + "\n" \
						+ _("Please verify if your default storage device is attached or set up your default storage device in menu -> setup -> system -> recording paths."), MessageBox.TYPE_ERROR)
					return
			else:
				# XXX: this message is a little odd as we might be recording to a remote device
				self.session.open(MessageBox, _("No HDD found or HDD not initialized!"), MessageBox.TYPE_ERROR)
				return

		if self.isInstantRecordRunning():
			self.session.openWithCallback(self.recordQuestionCallback, ChoiceBox, \
				title=_("A recording is currently running.\nWhat do you want to do?"), \
				list=((_("stop recording"), "stop"), \
				(_("add recording (stop after current event)"), "event"), \
				(_("add recording (indefinitely)"), "indefinitely"), \
				(_("add recording (enter recording duration)"), "manualduration"), \
				(_("add recording (enter recording endtime)"), "manualendtime"), \
				(_("change recording (duration)"), "changeduration"), \
				(_("change recording (endtime)"), "changeendtime"), \
				(_("Timeshift")+" "+_("save recording (stop after current event)"), "savetimeshift"), \
				(_("Timeshift")+" "+_("save recording (Select event)"), "savetimeshiftEvent"), \
				(_("do nothing"), "no")))
		else:
			self.session.openWithCallback(self.recordQuestionCallback, ChoiceBox, \
				title=_("Start recording?"), \
				list=((_("add recording (stop after current event)"), "event"), \
				(_("add recording (indefinitely)"), "indefinitely"), \
				(_("add recording (enter recording duration)"), "manualduration"), \
				(_("add recording (enter recording endtime)"), "manualendtime"), \
				(_("Timeshift")+" "+_("save recording (stop after current event)"), "savetimeshift"), \
				(_("Timeshift")+" "+_("save recording (Select event)"), "savetimeshiftEvent"), \
				(_("don't record"), "no")))

	def recordQuestionCallback(self, answer):
		InfoBarOrg.recordQuestionCallback(self, answer)

		if config.plugins.pts.enabled.value:
			if answer is not None and answer[1] == "savetimeshift":
				if self.isSeekable() and self.pts_eventcount != self.pts_currplaying:
					self.SaveTimeshift(timeshiftfile="pts_livebuffer.%s" % self.pts_currplaying)
				else:
					Notifications.AddNotification(MessageBox,_("Timeshift will get saved at end of event!"), MessageBox.TYPE_INFO, timeout=5)
					self.save_current_timeshift = True
					config.plugins.pts.isRecording.value = True
			if answer is not None and answer[1] == "savetimeshiftEvent":
				self.saveTimeshiftEventPopup()

			if answer is not None and answer[1].startswith("pts_livebuffer") is True:
				self.SaveTimeshift(timeshiftfile=answer[1])

	def ptsCleanTimeshiftFolder(self):
		if not config.plugins.pts.enabled.value or self.ptsCheckTimeshiftPath() is False or self.session.screen["Standby"].boolean is True:
			return

		try:
			for filename in os_listdir(config.usage.timeshift_path.value):
				if (filename.startswith("timeshift.") or filename.startswith("pts_livebuffer.")) and (filename.endswith(".del") is False and filename.endswith(".copy") is False and filename.endswith(".meta") is False and filename.endswith(".eit") is False):

					statinfo = os_stat("%s/%s" % (config.usage.timeshift_path.value,filename))
					# if no write for 5 sec = stranded timeshift
					if statinfo.st_mtime < (time()-5.0):
						Log.i("[PTS-Plugin] Erasing stranded timeshift %s" % filename)
						self.BgFileEraser.erase("%s/%s" % (config.usage.timeshift_path.value,filename))

						# Delete Meta and EIT File too
						if filename.startswith("pts_livebuffer.") is True:
							self.BgFileEraser.erase("%s/%s.meta" % (config.usage.timeshift_path.value,filename))
							self.BgFileEraser.erase("%s/%s.eit" % (config.usage.timeshift_path.value,filename))
		except:
			Log.i("[PTS-Plugin] IO-Error while cleaning Timeshift Folder ...")

	def ptsGetEvent(self):
		event = None
		try:
			serviceref = self.session.nav.getCurrentlyPlayingServiceReference()
			serviceHandler = eServiceCenter.getInstance()
			info = serviceHandler.info(serviceref)

			self.pts_curevent_servicerefname = serviceref.toString()
			self.pts_curevent_station = info.getName(serviceref)

			service = self.session.nav.getCurrentService()
			info = service and service.info()
			event = info and info.getEvent(0)
		except Exception, errormsg:
			Notifications.AddNotification(MessageBox, _("Getting Event Info failed!")+"\n\n%s" % errormsg, MessageBox.TYPE_ERROR, timeout=10)

		if event is not None:
			curEvent = parseEvent(event)
			return curEvent
		
		return None

	def ptsGetEventInfo(self):
		event = None
		try:
			serviceref = self.session.nav.getCurrentlyPlayingServiceReference()
			serviceHandler = eServiceCenter.getInstance()
			info = serviceHandler.info(serviceref)

			self.pts_curevent_servicerefname = serviceref.toString()
			self.pts_curevent_station = info.getName(serviceref)

			service = self.session.nav.getCurrentService()
			info = service and service.info()
			event = info and info.getEvent(0)
		except Exception, errormsg:
			Notifications.AddNotification(MessageBox, _("Getting Event Info failed!")+"\n\n%s" % errormsg, MessageBox.TYPE_ERROR, timeout=10)

		if event is not None:
			curEvent = parseEvent(event)
			self.pts_curevent_begin = int(curEvent[0])
			self.pts_curevent_end = int(curEvent[1])
			self.pts_curevent_name = curEvent[2]
			self.pts_curevent_description = curEvent[3]
			self.pts_curevent_eventid = curEvent[4]
			
			# save the eventinfo for the pts-files
			begin_time = int(self.pts_curevent_begin + int(config.recording.margin_before.value * 60) )
			before_pts = int(time() - begin_time -2)
			self.pts_list[str(self.pts_eventcount)]= {'begin': self.pts_curevent_begin,'end': self.pts_curevent_end, 'name': self.pts_curevent_name, 'description': self.pts_curevent_description, 'eventid': self.pts_curevent_eventid, 'before_pts': before_pts}
			
			# set new Eventname to normal PTS Infobar if current Live-Event
			if self.pts_currplaying == self.pts_eventcount:
				self.pvrStateDialog["eventname"].setText(self.pts_curevent_name)

	def ptsFrontpanelActions(self, action=None):
		if self.session.nav.RecordTimer.isRecording() or SystemInfo.get("NumFrontpanelLEDs", 0) == 0:
			return

		try:
			if action == "start":
				if fileExists("/proc/stb/fp/led_set_pattern"):
					open("/proc/stb/fp/led_set_pattern", "w").write("0xa7fccf7a")
				elif fileExists("/proc/stb/fp/led0_pattern"):
					open("/proc/stb/fp/led0_pattern", "w").write("0x55555555")
				if fileExists("/proc/stb/fp/led_pattern_speed"):
					open("/proc/stb/fp/led_pattern_speed", "w").write("20")
				elif fileExists("/proc/stb/fp/led_set_speed"):
					open("/proc/stb/fp/led_set_speed", "w").write("20")
			elif action == "stop":
				if fileExists("/proc/stb/fp/led_set_pattern"):
					open("/proc/stb/fp/led_set_pattern", "w").write("0")
				elif fileExists("/proc/stb/fp/led0_pattern"):
					open("/proc/stb/fp/led0_pattern", "w").write("0")
		except Exception, errormsg:
			Log.i("[PTS-Plugin] %s" % (errormsg))

	def ptsCreateHardlink(self):
		for filename in os_listdir(config.usage.timeshift_path.value):
			if filename.startswith("timeshift.") and not filename.endswith(".del") and not filename.endswith(".copy") and not filename.endswith(".sc"):
				try:
					statinfo = os_stat("%s/%s" % (config.usage.timeshift_path.value,filename))
					if statinfo.st_mtime > (time()-5.0):
						try:
							self.BgFileEraser.erase("%s/pts_livebuffer.%s" % (config.usage.timeshift_path.value,self.pts_eventcount))
							self.BgFileEraser.erase("%s/pts_livebuffer.%s.meta" % (config.usage.timeshift_path.value,self.pts_eventcount))
						except Exception, errormsg:
							Log.i("[PTS-Plugin] %s" % (errormsg))

						try:
							# Create link to pts_livebuffer file
							os_link("%s/%s" % (config.usage.timeshift_path.value,filename), "%s/pts_livebuffer.%s" % (config.usage.timeshift_path.value,self.pts_eventcount))

							# Create a Meta File
							metafile = open("%s/pts_livebuffer.%s.meta" % (config.usage.timeshift_path.value,self.pts_eventcount), "w")
							metafile.write("%s\n%s\n%s\n%i\n" % (self.pts_curevent_servicerefname,self.pts_curevent_name.replace("\n", ""),self.pts_curevent_description.replace("\n", ""),int(self.pts_starttime)))
							metafile.close()
						except Exception, errormsg:
							Notifications.AddNotification(MessageBox, _("Creating Hardlink to Timeshift file failed!")+"\n"+_("The Filesystem on your Timeshift-Device does not support hardlinks.\nMake sure it is formated in EXT2 or EXT3!")+"\n\n%s" % errormsg, MessageBox.TYPE_ERROR)

						# Create EIT File
						self.ptsCreateEITFile("%s/pts_livebuffer.%s" % (config.usage.timeshift_path.value,self.pts_eventcount))

						# Permanent Recording Hack
						if config.plugins.pts.permanentrecording.value:
							try:
								fullname = Directories.getRecordingFilename("%s - %s - %s" % (strftime("%Y%m%d %H%M",localtime(self.pts_starttime)),self.pts_curevent_station,self.pts_curevent_name),config.usage.default_path.value)
								os_link("%s/%s" % (config.usage.timeshift_path.value,filename), "%s.ts" % (fullname))
								# Create a Meta File
								metafile = open("%s.ts.meta" % (fullname), "w")
								metafile.write("%s\n%s\n%s\n%i\nautosaved\n" % (self.pts_curevent_servicerefname,self.pts_curevent_name.replace("\n", ""),self.pts_curevent_description.replace("\n", ""),int(self.pts_starttime)))
								metafile.close()
							except Exception, errormsg:
								Log.i("[PTS-Plugin] %s" % (errormsg))
				except Exception, errormsg:
					errormsg = str(errormsg)
					if errormsg.find('Input/output error') != -1:
						errormsg += _("\nAn Input/output error usually indicates a corrupted filesystem! Please check the filesystem of your timeshift-device!")
					Notifications.AddNotification(MessageBox, _("Creating Hardlink to Timeshift file failed!")+"\n%s" % (errormsg), MessageBox.TYPE_ERROR)

	def ptsRecordCurrentEvent(self):
			recording = RecordTimerEntry(ServiceReference(self.session.nav.getCurrentlyPlayingServiceReference()), time(), self.pts_curevent_end, self.pts_curevent_name, self.pts_curevent_description, self.pts_curevent_eventid, dirname = config.usage.default_path.value)
			recording.dontSave = True
			self.session.nav.RecordTimer.record(recording)
			self.recording.append(recording)

	def ptsMergeRecords(self):
		if self.session.nav.RecordTimer.isRecording():
			self.pts_mergeRecords_timer.start(120000, True)
			return

		ptsmergeSRC = ""
		ptsmergeDEST = ""
		ptsmergeeventname = ""
		ptsgetnextfile = False
		ptsfilemerged = False

		filelist = os_listdir(config.usage.default_path.value)

		if filelist is not None:
			filelist.sort()

		for filename in filelist:
			if filename.endswith(".meta"):
				# Get Event Info from meta file
				readmetafile = open("%s/%s" % (config.usage.default_path.value,filename), "r")
				servicerefname = readmetafile.readline()[0:-1]
				eventname = readmetafile.readline()[0:-1]
				eventtitle = readmetafile.readline()[0:-1]
				eventtime = readmetafile.readline()[0:-1]
				eventtag = readmetafile.readline()[0:-1]
				readmetafile.close()

				if ptsgetnextfile:
					ptsgetnextfile = False
					ptsmergeSRC = filename[0:-5]

					if ASCIItranslit.legacyEncode(eventname) == ASCIItranslit.legacyEncode(ptsmergeeventname):
						# Copy EIT File
						if fileExists("%s/%s.eit" % (config.usage.default_path.value, ptsmergeSRC[0:-3])):
							copyfile("%s/%s.eit" % (config.usage.default_path.value, ptsmergeSRC[0:-3]),"%s/%s.eit" % (config.usage.default_path.value, ptsmergeDEST[0:-3]))

						# Delete AP and SC Files
						self.BgFileEraser.erase("%s/%s.ap" % (config.usage.default_path.value, ptsmergeDEST))
						self.BgFileEraser.erase("%s/%s.sc" % (config.usage.default_path.value, ptsmergeDEST))

						# Add Merge Job to JobManager
						JobManager.AddJob(MergeTimeshiftJob(self, "cat \"%s/%s\" >> \"%s/%s\"" % (config.usage.default_path.value,ptsmergeSRC,config.usage.default_path.value,ptsmergeDEST), ptsmergeSRC, ptsmergeDEST, eventname))
						config.plugins.pts.isRecording.value = True
						ptsfilemerged = True
					else:
						ptsgetnextfile = True

				if eventtag == "pts_merge" and not ptsgetnextfile:
					ptsgetnextfile = True
					ptsmergeDEST = filename[0:-5]
					ptsmergeeventname = eventname
					ptsfilemerged = False

					# If still recording or transfering, try again later ...
					if fileExists("%s/%s" % (config.usage.default_path.value,ptsmergeDEST)):
						statinfo = os_stat("%s/%s" % (config.usage.default_path.value,ptsmergeDEST))
						if statinfo.st_mtime > (time()-10.0):
							self.pts_mergeRecords_timer.start(120000, True)
							return

					# Rewrite Meta File to get rid of pts_merge tag
					metafile = open("%s/%s.meta" % (config.usage.default_path.value,ptsmergeDEST), "w")
					metafile.write("%s\n%s\n%s\n%i\n" % (servicerefname,eventname.replace("\n", ""),eventtitle.replace("\n", ""),int(eventtime)))
					metafile.close()

		# Merging failed :(
		if not ptsfilemerged and ptsgetnextfile:
			Notifications.AddNotification(MessageBox,_("[PTS-Plugin] Merging records failed!"), MessageBox.TYPE_ERROR)

	def ptsCreateAPSCFiles(self, filename):
		if fileExists(filename, 'r'):
			if fileExists(filename+".meta", 'r'):
				# Get Event Info from meta file
				readmetafile = open(filename+".meta", "r")
				servicerefname = readmetafile.readline()[0:-1]
				eventname = readmetafile.readline()[0:-1]
			else:
				eventname = ""
			JobManager.AddJob(CreateAPSCFilesJob(self, "/usr/lib/enigma2/python/Plugins/Extensions/PermanentTimeshift/createapscfiles \"%s\"" % (filename), eventname))
		else:
			self.ptsSaveTimeshiftFinished()

	def ptsCreateEITFile(self, filename):
		if self.pts_curevent_eventid is not None:
			try:
				import eitsave
				serviceref = ServiceReference(self.session.nav.getCurrentlyPlayingServiceReference()).ref.toString()
				eitsave.SaveEIT(serviceref, filename+".eit", self.pts_curevent_eventid, -1, -1)
			except Exception, errormsg:
				Log.i("[PTS-Plugin] %s" % (errormsg))

	def ptsCopyFilefinished(self, srcfile, destfile):
		# Erase Source File
		if fileExists(srcfile):
			self.BgFileEraser.erase(srcfile)

		# Restart Merge Timer
		if self.pts_mergeRecords_timer.isActive():
			self.pts_mergeRecords_timer.stop()
			self.pts_mergeRecords_timer.start(15000, True)
		else:
			# Create AP and SC Files
			self.ptsCreateAPSCFiles(destfile)

	def ptsMergeFilefinished(self, srcfile, destfile):
		if self.session.nav.RecordTimer.isRecording() or len(JobManager.getPendingJobs()) >= 1:
			# Rename files and delete them later ...
			self.pts_mergeCleanUp_timer.start(120000, True)
			os_system("echo \"\" > \"%s.pts.del\"" % (srcfile[0:-3]))
		else:
			# Delete Instant Record permanently now ... R.I.P.
			self.BgFileEraser.erase("%s" % (srcfile))
			self.BgFileEraser.erase("%s.ap" % (srcfile))
			self.BgFileEraser.erase("%s.sc" % (srcfile))
			self.BgFileEraser.erase("%s.meta" % (srcfile))
			self.BgFileEraser.erase("%s.cuts" % (srcfile))
			self.BgFileEraser.erase("%s.eit" % (srcfile[0:-3]))

		# Create AP and SC Files
		self.ptsCreateAPSCFiles(destfile)

		# Run Merge-Process one more time to check if there are more records to merge
		self.pts_mergeRecords_timer.start(10000, True)

	def ptsSaveTimeshiftFinished(self):
		if not self.pts_mergeCleanUp_timer.isActive():
			self.ptsFrontpanelActions("stop")
			config.plugins.pts.isRecording.value = False

		if Screens.Standby.inTryQuitMainloop:
			self.pts_QuitMainloop_timer.start(30000, True)
		else:
			Notifications.AddNotification(MessageBox, _("Timeshift saved to your harddisk!"), MessageBox.TYPE_INFO, timeout = 5)

	def ptsMergePostCleanUp(self):
		if self.session.nav.RecordTimer.isRecording() or len(JobManager.getPendingJobs()) >= 1:
			config.plugins.pts.isRecording.value = True
			self.pts_mergeCleanUp_timer.start(120000, True)
			return

		self.ptsFrontpanelActions("stop")
		config.plugins.pts.isRecording.value = False

		filelist = os_listdir(config.usage.default_path.value)
		for filename in filelist:
			if filename.endswith(".pts.del"):
				srcfile = config.usage.default_path.value + "/" + filename[0:-8] + ".ts"
				self.BgFileEraser.erase("%s" % (srcfile))
				self.BgFileEraser.erase("%s.ap" % (srcfile))
				self.BgFileEraser.erase("%s.sc" % (srcfile))
				self.BgFileEraser.erase("%s.meta" % (srcfile))
				self.BgFileEraser.erase("%s.cuts" % (srcfile))
				self.BgFileEraser.erase("%s.eit" % (srcfile[0:-3]))
				self.BgFileEraser.erase("%s.pts.del" % (srcfile[0:-3]))

				# Restart QuitMainloop Timer to give BgFileEraser enough time
				if Screens.Standby.inTryQuitMainloop and self.pts_QuitMainloop_timer.isActive():
					self.pts_QuitMainloop_timer.start(60000, True)

	def ptsTryQuitMainloop(self):
		if Screens.Standby.inTryQuitMainloop and (len(JobManager.getPendingJobs()) >= 1 or self.pts_mergeCleanUp_timer.isActive()):
			self.pts_QuitMainloop_timer.start(60000, True)
			return

		if Screens.Standby.inTryQuitMainloop and self.session.ptsmainloopvalue:
			self.session.dialog_stack = []
			self.session.summary_stack = [None]
			self.session.open(TryQuitMainloop, self.session.ptsmainloopvalue)

	def ptsGetSeekInfo(self):
		s = self.session.nav.getCurrentService()
		return s and s.seek()

	def ptsGetPosition(self):
		seek = self.ptsGetSeekInfo()
		if seek is None:
			return None
		pos = seek.getPlayPosition()
		if pos[0]:
			return 0
		return pos[1]

	def ptsGetLength(self):
		seek = self.ptsGetSeekInfo()
		if seek is None:
			return None
		length = seek.getLength()
		if length[0]:
			return 0
		return length[1]

	def ptsGetSaveTimeshiftStatus(self):
		return self.save_current_timeshift

	def ptsSeekPointerOK(self):
		if self.pts_pvrStateDialog == "PTSTimeshiftState" and self.timeshift_enabled and self.isSeekable():
			if not self.pvrstate_hide_timer.isActive():
				if self.seekstate != self.SEEK_STATE_PLAY:
					self.setSeekState(self.SEEK_STATE_PLAY)
				self.doShow()
				return

			length = self.ptsGetLength()
			position = self.ptsGetPosition()

			if length is None or position is None:
				return

			cur_pos = self.pvrStateDialog["PTSSeekPointer"].position
			jumptox = int(cur_pos[0]) - int(self.pts_seekpointer_MinX)
			jumptoperc = round((jumptox / 400.0) * 100, 0)
			jumptotime = int((length / 100) * jumptoperc)
			jumptodiff = position - jumptotime

			self.doSeekRelative(-jumptodiff)
		else:
			return

	def ptsSeekPointerLeft(self):
		if self.pts_pvrStateDialog == "PTSTimeshiftState" and self.timeshift_enabled and self.isSeekable():
			self.ptsMoveSeekPointer(direction="left")
		else:
			return

	def ptsSeekPointerRight(self):
		if self.pts_pvrStateDialog == "PTSTimeshiftState" and self.timeshift_enabled and self.isSeekable():
			self.ptsMoveSeekPointer(direction="right")
		else:
			return

	def ptsSeekPointerReset(self):
		if self.pts_pvrStateDialog == "PTSTimeshiftState" and self.timeshift_enabled:
			self.pvrStateDialog["PTSSeekPointer"].setPosition(self.pts_seekpointer_MinX,self.pvrStateDialog["PTSSeekPointer"].position[1])

	def ptsSeekPointerSetCurrentPos(self):
		if not self.pts_pvrStateDialog == "PTSTimeshiftState" or not self.timeshift_enabled or not self.isSeekable():
			return

		position = self.ptsGetPosition()
		length = self.ptsGetLength()

		if length >= 1:
			tpixels = int((float(int((position*100)/length))/100)*400)
			self.pvrStateDialog["PTSSeekPointer"].setPosition(self.pts_seekpointer_MinX+tpixels, self.pvrStateDialog["PTSSeekPointer"].position[1])

	def ptsMoveSeekPointer(self, direction=None):
		if direction is None or self.pts_pvrStateDialog != "PTSTimeshiftState":
			return

		isvalidjump = False
		cur_pos = self.pvrStateDialog["PTSSeekPointer"].position
		InfoBarOrg._mayShow(self)

		if direction == "left":
			minmaxval = self.pts_seekpointer_MinX
			movepixels = -15
			if cur_pos[0]+movepixels > minmaxval:
				isvalidjump = True
		elif direction == "right":
			minmaxval = self.pts_seekpointer_MaxX
			movepixels = 15
			if cur_pos[0]+movepixels < minmaxval:
				isvalidjump = True
		else:
			return 0

		if isvalidjump:
			self.pvrStateDialog["PTSSeekPointer"].setPosition(cur_pos[0]+movepixels,cur_pos[1])
		else:
			self.pvrStateDialog["PTSSeekPointer"].setPosition(minmaxval,cur_pos[1])

	def ptsTimeshiftFileChanged(self):
		# Reset Seek Pointer
		if config.plugins.pts.enabled.value and config.plugins.pts.showinfobar.value:
			self.ptsSeekPointerReset()

		if self.pts_switchtolive:
			self.pts_switchtolive = False
			return

		if self.pts_seektoprevfile:
			if self.pts_currplaying == 1:
				self.pts_currplaying = config.plugins.pts.maxevents.value
			else:
				self.pts_currplaying -= 1
		else:
			if self.pts_currplaying == config.plugins.pts.maxevents.value:
				self.pts_currplaying = 1
			else:
				self.pts_currplaying += 1

		if not fileExists("%s/pts_livebuffer.%s" % (config.usage.timeshift_path.value,self.pts_currplaying), 'r'):
			self.pts_currplaying = self.pts_eventcount
		
		# Set Eventname in TimeshiftState-Screen
		try:
			eventname = self.pts_list[str(self.pts_currplaying)]['name']
			self.pvrStateDialog["eventname"].setText(eventname)
			self.session.summary["eventname"].setText(eventname)
			
		except:
			pass

		# Set Eventname in PTS InfoBar
		if config.plugins.pts.enabled.value and config.plugins.pts.showinfobar.value and self.pts_pvrStateDialog == "PTSTimeshiftState":
			try:
				if self.pts_eventcount != self.pts_currplaying:
					readmetafile = open("%s/pts_livebuffer.%s.meta" % (config.usage.timeshift_path.value,self.pts_currplaying), "r")
					servicerefname = readmetafile.readline()[0:-1]
					eventname = readmetafile.readline()[0:-1]
					readmetafile.close()
					self.pvrStateDialog["eventname"].setText(eventname)
				else:
					self.pvrStateDialog["eventname"].setText("")
			except Exception, errormsg:
				self.pvrStateDialog["eventname"].setText("")

		# show pvrStateDialog after change timeshiftfile
		self.pvrStateDialog.show()

		self.pvrstate_hide_timer = eTimer()
		self.pvrstate_hide_timer_conn = self.pvrstate_hide_timer.timeout.connect(self.pvrStateDialog.hide)
		self.pvrstate_hide_timer.stop()

		if self.seekstate == self.SEEK_STATE_PLAY:
			idx = config.usage.infobar_timeout.index
			if not idx:
				idx = 5
			self.pvrstate_hide_timer.start(idx*1000, True)
		else:
			self.pvrstate_hide_timer.stop()
		# Ende show pvrStateDialog
		
		# Get next pts file ...
		if self.pts_currplaying+1 > config.plugins.pts.maxevents.value:
			nextptsfile = 1
		else:
			nextptsfile = self.pts_currplaying+1

		if self.pts_currplaying != self.pts_eventcount and fileExists("%s/pts_livebuffer.%s" % (config.usage.timeshift_path.value,nextptsfile), 'r'):
			self.ptsSetNextPlaybackFile("pts_livebuffer.%s" % (nextptsfile))
		else:
			self.pts_switchtolive = True
			self.ptsSetNextPlaybackFile(None)

		self.ptsHandleSeekBackward()

	def ptsHandleSeekBackward(self):
		if self.seekstate[1] < 0:
			if self.pts_currplaying == 1:
				preptsfile = config.plugins.pts.maxevents.value
			else:
				preptsfile = self.pts_currplaying-1
			if preptsfile != self.pts_eventcount and fileExists("%s/pts_livebuffer.%s" % (config.usage.timeshift_path.value, preptsfile), 'r'):
				self.pts_switchtolive = False
				self.pts_seektoprevfile = True
				self.ptsSetPrevPlaybackFile("pts_livebuffer.%s" %preptsfile)
				return
		if self.pts_seektoprevfile:
			self.pts_seektoprevfile = False
			self.ptsSetPrevPlaybackFile(None)

	def ptsSetNextPlaybackFile(self, nexttsfile):
		ts = self.getTimeshift()
		if ts is None:
			return

		# Set Eventname in TimeshiftState-Screen
		try:
			if self.pts_eventcount==self.pts_currplaying: # set only on current timeshiftfile
				eventname = self.pts_list[str(self.pts_currplaying)]['name']
				self.pvrStateDialog["eventname"].setText(eventname)
				self.session.summary["eventname"].setText(eventname)
		except:
			pass

		try:
			next_file = "%s/%s" % (config.usage.timeshift_path.value, nexttsfile) if nexttsfile else ""
			Log.i("[PTS-Plugin] setNextPlaybackFile(%s)" % next_file)
			ts.setNextPlaybackFile(next_file)
			#== fix for set switchtolive
			if nexttsfile and self.pts_currplaying != self.pts_eventcount:
				self.pts_switchtolive=False
		except:
			Log.i("[PTS-Plugin] setNextPlaybackFile() not supported by OE. Enigma2 too old !?")

	def ptsSetPrevPlaybackFile(self, prevtsfile):
		ts = self.getTimeshift()
		if ts is None:
			return

		try:
			prev_file = "%s/%s" % (config.usage.timeshift_path.value, prevtsfile) if prevtsfile else ""
			Log.i("[PTS-Plugin] setPrevPlaybackFile(%s)" % prev_file)
			ts.setPrevPlaybackFile(prev_file)
		except:
			Log.i("[PTS-Plugin] setPrevPlaybackFile() not supported by OE. Enigma2 too old !?")

	def ptsSeekBackTimer(self):
		if self.pts_lastseekspeed == 0:
			self.setSeekState(self.makeStateBackward(int(config.seek.enter_backward.value)))
		else:
			self.setSeekState(self.makeStateBackward(int(-self.pts_lastseekspeed)))

	def ptsCheckTimeshiftPath(self):
		if self.pts_pathchecked:
			return True
		else:
			if fileExists(config.usage.timeshift_path.value, 'w'):
				self.pts_pathchecked = True
				return True
			else:
				Notifications.AddNotification(MessageBox, _("Could not activate Permanent-Timeshift!\nTimeshift-Path does not exist"), MessageBox.TYPE_ERROR, timeout=15)
				if self.pts_delay_timer.isActive():
					self.pts_delay_timer.stop()
				if self.pts_cleanUp_timer.isActive():
					self.pts_cleanUp_timer.stop()
				return False

	def ptsTimerEntryStateChange(self, timer):
		if not config.plugins.pts.enabled.value or not config.plugins.pts.stopwhilerecording.value:
			return

		self.pts_record_running = self.session.nav.RecordTimer.isRecording()

		# Abort here when box is in standby mode
		if self.session.screen["Standby"].boolean is True:
			return

		# Stop Timeshift when Record started ...
		if timer.state == TimerEntry.StateRunning and self.timeshift_enabled and self.pts_record_running:
			if self.ptsLiveTVStatus() is False:
				self.timeshift_enabled = 0
				self.pts_LengthCheck_timer.stop()
				return

			if self.seekstate != self.SEEK_STATE_PLAY:
				self.setSeekState(self.SEEK_STATE_PLAY)

			if self.isSeekable():
				Notifications.AddNotification(MessageBox,_("Record started! Stopping timeshift now ..."), MessageBox.TYPE_INFO, timeout=5)

			self.stopTimeshiftConfirmed(True, False)

		# Restart Timeshift when all records stopped
		if timer.state == TimerEntry.StateEnded and not self.timeshift_enabled and not self.pts_record_running:
			self.activatePermanentTimeshift()

		# Restart Merge-Timer when all records stopped
		if timer.state == TimerEntry.StateEnded and self.pts_mergeRecords_timer.isActive():
			self.pts_mergeRecords_timer.stop()
			self.pts_mergeRecords_timer.start(15000, True)

		# Restart FrontPanel LED when still copying or merging files
		# ToDo: Only do this on PTS Events and not events from other jobs
		if timer.state == TimerEntry.StateEnded and (len(JobManager.getPendingJobs()) >= 1 or self.pts_mergeRecords_timer.isActive()):
			self.ptsFrontpanelActions("start")
			config.plugins.pts.isRecording.value = True

	def ptsLiveTVStatus(self):
		service = self.session.nav.getCurrentService()
		info = service and service.info()
		sTSID = info and info.getInfo(iServiceInformation.sTSID) or -1

		if sTSID is None or sTSID == -1:
			return False
		else:
			return True

	def ptsLengthCheck(self):
		# Check if we are in TV Mode ...
		if self.ptsLiveTVStatus() is False:
			self.timeshift_enabled = 0
			self.pts_LengthCheck_timer.stop()
			return

		if config.plugins.pts.stopwhilerecording.value and self.pts_record_running:
			return

		# Length Check
		if config.plugins.pts.enabled.value and self.session.screen["Standby"].boolean is not True and self.timeshift_enabled and (time() - self.pts_starttime) >= (config.plugins.pts.maxlength.value * 60):
			if self.save_current_timeshift:
				self.saveTimeshiftActions("savetimeshift")
				self.activatePermanentTimeshift()
				self.save_current_timeshift = True
			else:
				self.activatePermanentTimeshift()
			Notifications.AddNotification(MessageBox,_("Maximum Timeshift length per Event reached!\nRestarting Timeshift now ..."), MessageBox.TYPE_INFO, timeout=5)

#Replace the InfoBar with our version ;)
Screens.InfoBar.InfoBar = InfoBar

################################
##### Class Standby Hack 1 #####
################################
TryQuitMainloop_getRecordEvent = Screens.Standby.TryQuitMainloop.getRecordEvent

class TryQuitMainloopPTS(TryQuitMainloop):
	def __init__(self, session, retvalue=1, timeout=-1, default_yes = True):
		TryQuitMainloop.__init__(self, session, retvalue, timeout, default_yes)

		self.session.ptsmainloopvalue = retvalue

	def getRecordEvent(self, recservice, event):
		if event == iRecordableService.evEnd and (config.plugins.pts.isRecording.value or len(JobManager.getPendingJobs()) >= 1):
			return
		else:
			TryQuitMainloop_getRecordEvent(self, recservice, event)

Screens.Standby.TryQuitMainloop = TryQuitMainloopPTS

################################
##### Class Standby Hack 2 #####
################################

Screens_Standby_Standby = Screens.Standby.Standby

class StandbyPTS(Standby):
	def __init__(self, session):
		if InfoBar and InfoBar.instance and InfoBar.ptsGetSaveTimeshiftStatus(InfoBar.instance):
			self.skin = """<screen position="0,0" size="0,0"/>"""
			Screen.__init__(self, session)
			self.onFirstExecBegin.append(self.showMessageBox)
			self.onHide.append(self.close)
		else:
			Standby.__init__(self, session)
			self.skinName = "Standby"

	def showMessageBox(self):
		if InfoBar and InfoBar.instance:
			InfoBar.saveTimeshiftActions(InfoBar.instance, postaction="standby")

Screens.Standby.Standby = StandbyPTS

#############################
# getNextRecordingTime Hack #
#############################
RecordTimer_getNextRecordingTime = RecordTimer.getNextRecordingTime

def getNextRecordingTime(self):
	nextrectime = RecordTimer_getNextRecordingTime(self)
	faketime = time()+300

	if config.plugins.pts.isRecording.value or len(JobManager.getPendingJobs()) >= 1:
		if nextrectime > 0 and nextrectime < faketime:
			return nextrectime
		else:
			return faketime
	else:
		return nextrectime

RecordTimer.getNextRecordingTime = getNextRecordingTime

############################
#####  SETTINGS SCREEN #####
############################
class PermanentTimeShiftSetup(Screen, ConfigListScreen):
	def __init__(self, session):
		Screen.__init__(self, session)
		self.skinName = [ "PTSSetup", "Setup" ]
		self.setup_title = _("Permanent Timeshift Settings Version %s") %VERSION

		self.onChangedEntry = [ ]
		self.list = [ ]
		ConfigListScreen.__init__(self, self.list, session = session, on_change = self.changedEntry)

		self["actions"] = ActionMap(["SetupActions", "ColorActions"],
		{
			"ok": self.SaveSettings,
			"green": self.SaveSettings,
			"red": self.Exit,
			"cancel": self.Exit
		}, -2)

		self["key_green"] = StaticText(_("OK"))
		self["key_red"] = StaticText(_("Cancel"))

		self.createSetup()
		self.onLayoutFinish.append(self.layoutFinished)

	def layoutFinished(self):
		self.setTitle(self.setup_title)

	def createSetup(self):
		self.list = [ getConfigListEntry(_("Permanent Timeshift Enable"), config.plugins.pts.enabled) ]
		if config.plugins.pts.enabled.value:
			self.list.extend((
				getConfigListEntry(_("Permanent Timeshift Max Events"), config.plugins.pts.maxevents),
				getConfigListEntry(_("Permanent Timeshift Max Length"), config.plugins.pts.maxlength),
				getConfigListEntry(_("Permanent Timeshift Start Delay"), config.plugins.pts.startdelay),
				getConfigListEntry(_("Timeshift-Save Action on zap"), config.plugins.pts.favoriteSaveAction),
				getConfigListEntry(_("Stop timeshift while recording?"), config.plugins.pts.stopwhilerecording),
				getConfigListEntry(_("Show PTS Infobar while timeshifting?"), config.plugins.pts.showinfobar),
				getConfigListEntry(_("Show real remainingtime while timeshifting?"), config.plugins.pts.showrealremainingtime),
				getConfigListEntry(_("Show zap-warning on active timeshifting?"), config.plugins.pts.showzapwarning)
			))
			if config.plugins.pts.showzapwarning.value == "simple":
				self.list.append(getConfigListEntry(_("   select default answer for zap-warning-question"), config.plugins.pts.zapwarning_defaultanswer1))
			elif config.plugins.pts.showzapwarning.value == "advanced":
				self.list.append(getConfigListEntry(_("   select default answer for zap-warning-question"), config.plugins.pts.zapwarning_defaultanswer2))
		
		# Permanent Recording Hack
		if fileExists("/usr/lib/enigma2/python/Plugins/Extensions/HouseKeeping/plugin.py"):
			self.list.append(getConfigListEntry(_("Beta: Enable Permanent Recording?"), config.plugins.pts.permanentrecording))
		
		self["config"].list = self.list
		self["config"].setList(self.list)

	def changedEntry(self):
		for x in self.onChangedEntry:
			x()
		current = self["config"].getCurrent()[1]
		if (current == config.plugins.pts.enabled) or (current == config.plugins.pts.showzapwarning):
			self.createSetup()
			return

	def getCurrentEntry(self):
		return self["config"].getCurrent()[0]

	def getCurrentValue(self):
		return str(self["config"].getCurrent()[1].getText())

	def createSummary(self):
		return SetupSummary

	def SaveSettings(self):
		config.plugins.pts.save()
		configfile.save()
		self.close()

	def Exit(self):
		self.close()

#################################################

def startSetup(menuid):
	if menuid != "services_recordings":
		return [ ]
	return [(_("Timeshift Settings"), PTSSetupMenu, "pts_setup", 50)]

def PTSSetupMenu(session, **kwargs):
	session.open(PermanentTimeShiftSetup)

def Plugins(path, **kwargs):
	return [ PluginDescriptor(name=_("Permanent Timeshift Settings"), description=_("Permanent Timeshift Settings"), where=PluginDescriptor.WHERE_MENU, fnc=startSetup) ]
