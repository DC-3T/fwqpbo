import numpy as np
import ctypes
import os
import dicom
import datetime
import sys
import configparser
import optparse
import scipy.io

# Helper class for convenient reading of config files
class AttrDict(dict):
	def __init__(self, *args, **kwargs):
		super(AttrDict, self).__init__(*args, **kwargs)
		self.__dict__ = self

IMGTYPE = ctypes.c_float
gyro = 42.58 # 1H gyromagnetic ratio

# Dictionary of DICOM tags
tagDict = {	'Image Type':0x00080008,
			'SOP Class UID':0x00080016,
			'SOP Instance UID':0x00080018,
			'Series Description':0x0008103E,
			'Slice Thickness':0x00180050,
			'Echo Time':0x00180081,
			'Imaging Frequency':0x00180084,
			'Protocol Name':0x00181030,
			'Study Instance UID':0x0020000D,
			'Series Instance UID':0x0020000E,
			'Series Number':0x00200011,
			'Slice Location':0x00201041,
			'Image Position (Patient)': 0x00200032,
			'Rows':0x00280010,
			'Columns':0x00280011,
			'Pixel Spacing':0x00280030,
			'Smallest Pixel Value':0x00280106,
			'Largest Pixel Value':0x00280107,
			'Window Center':0x00281050,
			'Window Width':0x00281051,
			'Rescale Intercept':0x00281052,
			'Rescale Slope':0x00281053,
			'Number of frames': 0x00280008,
			'Frame sequence': 0x52009230} #Per-frame Functional Groups Sequence

def getSOPInstanceUID():
	datestr = '%(year)04d%(month)02d%(day)02d%(hour)02d%(min)02d%(sec)02d%(msec)03d' % {"year":datetime.datetime.now().year,"month":datetime.datetime.now().month,"day":datetime.datetime.now().day,"hour":datetime.datetime.now().hour,"min":datetime.datetime.now().minute,"sec":datetime.datetime.now().second, "msec":datetime.datetime.now().microsecond/1000}
	uidstr = "1.3.12.2.1107.5.2.32.35356." + datestr + str(np.random.randint(1000,1000000000))
	return uidstr
	
def getSeriesInstanceUID():
	datestr = '%(year)04d%(month)02d%(day)02d%(hour)02d%(min)02d%(sec)02d%(msec)03d' % {"year":datetime.datetime.now().year,"month":datetime.datetime.now().month,"day":datetime.datetime.now().day,"hour":datetime.datetime.now().hour,"min":datetime.datetime.now().minute,"sec":datetime.datetime.now().second, "msec":datetime.datetime.now().microsecond/1000}
	uidstr = "1.3.12.2.1107.5.2.32.35356." + datestr + str(np.random.randint(1000,1000000000)) + ".0.0.0"
	return uidstr

# Set window so that 95% of pixels are inside
def get95percentileWindow(im):
	lims=np.percentile(im,[2.5,97.5])
	width = lims[1]-lims[0]
	center = width/2.+lims[0]
	return center,width
	
# Sets DICOM element value in dataset ds at tag=key. Use frame for multiframe DICOM files. If missing tag, a new one is created if value representation VR is provided
def setTagValue(ds,key,val,frame=None,VR=None):
	# TODO: improve support for multi-frame DICOM images
	if frame is not None and 0x2005140f in ds[tagDict['Frame sequence']].value[frame]: # Philips(?) private tag containing frame tags
		frameObject = ds[tagDict['Frame sequence']].value[frame][0x2005140f][0]
		if tagDict[key] in frameObject: 
			frameObject[tagDict[key]].value = val
			return True
	if tagDict[key] in ds: 
		ds[tagDict[key]].value = val
		return True
	# Else, add as new DICOM element:
	if VR:
		if frame is not None and 0x2005140f in ds[tagDict['Frame sequence']].value[frame]: # Philips(?) private tag containing frame tags
			frameObject = ds[tagDict['Frame sequence']].value[frame][0x2005140f][0]
			frameObject.add_new(tagDict[key],VR,val)
			return True
		ds.add_new(tagDict[key],VR,val)
		return True
	#print('Warning: DICOM tag {} was not set'.format(key))
	return False

# Save numpy array to DICOM image. Based on input DICOM image if exists, else create from scratch
def save(outDir,image,dPar,reScaleIntercept,reScaleSlope,seriesDescription,seriesNumber):
	print(r'Writing image{} to "{}"'.format('s'*(dPar.nz>1),outDir))
	if (reScaleSlope==None): #Calculate reScaleSlope based on image data
		reScaleSlope=np.max(image)/2**15
		print('reScaleSlope calculated to: ',reScaleSlope)
	image.shape = (dPar.nz,dPar.ny,dPar.nx)
	if not os.path.isdir(outDir): os.mkdir(outDir)
	seriesInstanceUID=getSeriesInstanceUID()
	multiframe = dPar.frameList and len(set([frame[0] for frame in dPar.frameList]))==1 # Single file is interpreted as multi-frame
	if multiframe: 
		ds = dicom.read_file(dPar.frameList[0][0])
		imVol = np.empty([dPar.nz,dPar.ny*dPar.nx],dtype='uint16')
		frames = []
	if dPar.frameList: imType = getType(dPar.frameList)
	for z,slice in enumerate(dPar.sliceList):
		filename = outDir+r'/{}.dcm'.format(slice)
		# Prepare pixel data:
		#TODO: Are reScale intercept and slope implemented according to DICOM standard?
		im = np.array([max(0,(val-reScaleIntercept)/reScaleSlope) for val in image[z,:,:].flatten()]) #Truncate and scale
		im = im.astype('uint16')
		windowCenter,windowWidth=get95percentileWindow(im) # Sets window so that 95% of pixels are inside
		if dPar.frameList:
			# Get frame
			frame = dPar.frameList[dPar.totalN*slice*len(imType)]
			iFrame = frame[1]
			if not multiframe: ds = dicom.read_file(frame[0])
		else:
			iFrame = None
			# Create new DICOM images from scratch
			file_meta = dicom.dataset.Dataset()
			file_meta.MediaStorageSOPClassUID = 'Secondary Capture Image Storage'
			file_meta.MediaStorageSOPInstanceUID = '1.3.6.1.4.1.9590.100.1.1.111165684411017669021768385720736873780'
			file_meta.ImplementationClassUID = '1.3.6.1.4.1.9590.100.1.0.100.4.0'
			ds = dicom.dataset.FileDataset(filename, {},file_meta = file_meta,preamble=b"\0"*128)
			# Add DICOM tags:
			ds.Modality = 'WSD'
			ds.ContentDate = str(datetime.date.today()).replace('-','')
			ds.ContentTime = str(datetime.time()) #milliseconds since the epoch
			ds.SamplesPerPixel = 1
			ds.PhotometricInterpretation = "MONOCHROME2"
			ds.PixelRepresentation = 0
			ds.HighBit = 15
			ds.BitsStored = 16
			ds.BitsAllocated = 16
			ds.SmallestImagePixelValue = '\\x00\\x00'
			ds.LargestImagePixelValue = '\\xff\\xff'
			ds.Columns = dPar.nx
			ds.Rows = dPar.ny
			setTagValue(ds,'Study Instance UID',getSOPInstanceUID(),iFrame,'UI')
		# Change/add DICOM tags:
		setTagValue(ds,'SOP Instance UID',getSOPInstanceUID(),iFrame,'UI')
		setTagValue(ds,'SOP Class UID','Secondary Capture Image Storage',iFrame,'UI')
		setTagValue(ds,'Series Instance UID',seriesInstanceUID,iFrame,'UI')
		setTagValue(ds,'Series Number',seriesNumber,iFrame,'IS')
		setTagValue(ds,'Echo Time',0.,iFrame,'DS')
		setTagValue(ds,'Protocol Name','Derived Image',iFrame,'LO')
		setTagValue(ds,'Series Description',seriesDescription,iFrame,'LO')
		setTagValue(ds,'Smallest Pixel Value',np.min(im),iFrame)
		setTagValue(ds,'Largest Pixel Value',np.max(im),iFrame)
		setTagValue(ds,'Window Center',int(windowCenter),iFrame,'DS')
		setTagValue(ds,'Window Width',int(windowWidth),iFrame,'DS')
		setTagValue(ds,'Rescale Intercept',reScaleIntercept,iFrame,'DS')
		setTagValue(ds,'Rescale Slope',reScaleSlope,iFrame,'DS')
			
		if multiframe: 
			imVol[z]=im
			frames.append(iFrame)
		else: 
			ds.PixelData = im
			ds.save_as(filename)
			
	if multiframe:
		setTagValue(ds,'SOP Instance UID',getSOPInstanceUID())
		setTagValue(ds,'Number of frames',len(frames))
		ds[tagDict['Frame sequence']].value = [ds[tagDict['Frame sequence']].value[frame] for frame in frames]
		ds.PixelData = imVol
		filename = outDir+r'/0.dcm'
		ds.save_as(filename)

# Check if ds is a multiframe DICOM object
def isMultiFrame(ds):
	return tagDict['Number of frames'] in ds and int(ds[tagDict['Number of frames']].value)>1 and tagDict['Frame sequence'] in ds

# Get list of all files in directories in dirList
def getFiles(dirList):
	files = [] # Get list of files:
	for dir in dirList:	files=files+[os.path.join(dir,file) for file in os.listdir(dir)]
	return files
	
# Retrieves DICOM element value from dataset ds at tag=key. Use frame for multiframe DICOM files
def getTagValue(ds,key,frame=None):
	if frame is not None and 0x2005140f in ds[tagDict['Frame sequence']].value[frame]: # Philips(?) private tag containing frame tags
		frameObject = ds[tagDict['Frame sequence']].value[frame][0x2005140f][0]
		if tagDict[key] in frameObject: return frameObject[tagDict[key]].value
	if tagDict[key] in ds: return ds[tagDict[key]].value
	return None

# Translates image type tag to M/P/R/I for magnitude/phase/real/imaginary
def typeTag2type(tagValue):
	for type in ['M','P','R','I']:
		if type in tagValue: return type
	return None
	
# Retrieve attribute from dataset ds. Use frame for multiframe DICOM files. Must attributes are read directly from their corresponding DICOM tag
def getAttribute(ds,attr,frame=None):
	attribute = getTagValue(ds,attr,frame)
	if attr == 'Slice Location' and attribute is None: 
		attribute = getTagValue(ds,'Image Position (Patient)',frame)
		if attribute: attribute = attribute[2]
	elif attr == 'Image Type' and attribute: attribute = typeTag2type(attribute)
	return attribute
	
# Check if attribute is in DICOM dataset ds
def AttrInDataset(ds,attr,multiframe):
	if getAttribute(ds,attr) is not None: return True
	elif multiframe:
		for frame in range(len(ds[tagDict['Frame sequence']].value)):
			if not getAttribute(ds,attr,frame): return False # Attribute must be in all frames!
		return True
	return False

# List of DICOM attributes required for the water-fat separation
reqAttributes = ['Image Type','Echo Time','Slice Location','Imaging Frequency','Columns','Rows','Pixel Spacing','Slice Thickness']

# Checks if list of DICOM files contains required information
def isValidDataset(files,printOutput=False):
	frameList = []
	for file in files:
		ds = dicom.read_file(file,stop_before_pixels=True)
		multiframe = isMultiFrame(ds)
		if multiframe: # Multi-frame DICOM files
			if len(files)>1: raise Exception('Support for multiple multi-frame DICOM files not implemented yet!')
			for frame in range(len(ds[tagDict['Frame sequence']].value)):
				frameList.append([file]+[frame]+[getAttribute(ds,attr,frame) for attr in reqAttributes])
		else: # Single-frame DICOM files
			frameList.append([file]+[None]+[getAttribute(ds,attr) for attr in reqAttributes])
	try: type = getType(frameList)
	except Exception as e:
		if printOutput: print(e)
		return False
	if len(set([tags[3] for tags in frameList]))<3:
		if printOutput: print('Error: Less than three echo times in dataset')
		return False
	if len(set([tags[5] for tags in frameList]))>1: 
		if printOutput: print('Error: Multiple imaging frequencies in dataset')
		return False
	if len(set([tags[6] for tags in frameList]))>1: 
		if printOutput: print('Error: Multiple image sizes (y-dir) in dataset')
		return False
	if len(set([tags[7] for tags in frameList]))>1: 
		if printOutput: print('Error: Multiple image sizes (x-dir) in dataset')
		return False
	if len(set([tags[8][0] for tags in frameList]))>1: 
		if printOutput: print('Error: Multiple voxel sizes (y-dir) in dataset')
		return False
	if len(set([tags[8][1] for tags in frameList]))>1: 
		if printOutput: print('Error: Multiple voxel sizes (x-dir) in dataset')
		return False
	if len(set([tags[9] for tags in frameList]))>1: 
		if printOutput: print('Error: Multiple slice thicknesses in dataset')
		return False
	return True

# Extract files that are readable and have all required DICOM tags
def getValidFiles(files,printOutput=False):
	validFiles = []
	for file in files:
		try: ds = dicom.read_file(file,stop_before_pixels=True)
		except:
			if printOutput: print('Could not read file: {}'.format(file))
			continue
		multiframe = isMultiFrame(ds)
		hasRequiredAttrs = [AttrInDataset(ds,attr,multiframe) for attr in reqAttributes]
		if not all(hasRequiredAttrs): 
			if printOutput: 
				print('File {} is missing required DICOM tags:'.format(file))
				for i,hasAttr in enumerate(hasRequiredAttrs): 
					if not hasAttr: print(reqAttributes[i])
			continue
		else: validFiles.append(file)
	return validFiles

# get combination of image types for DICOM frames in frameList
def getType(frameList, printType=False):
	typeTags = [tags[2] for tags in frameList]
	numR=typeTags.count('R')
	numI=typeTags.count('I')
	numM=typeTags.count('M')
	numP=typeTags.count('P')
	if numM+numP==0 and numR+numI>0 and numR==numI:
		if printType: print('Real/Imaginary images')
		return 'RI'
	elif numM+numP>0 and numR+numI==0 and numM==numP:
		if printType: print('Magnitude/Phase images')
		return 'MP'
	elif numP==0 and numM+numR+numI>0 and numM==numR==numI:
		if printType: print('Magnitude/Real/Imaginary images')
		return 'MRI'
	else: raise Exception('Unknown combination of image types: {} real, {} imag, {} magn, {} phase'.format(numR,numI,numM,numP))

# update dPar with information retrieved from the DICOM files including image data
def updateDataParamsDICOM(dPar,files):
	frameList = []
	for file in files:
		ds = dicom.read_file(file,stop_before_pixels=True)
		multiframe = isMultiFrame(ds)
		if multiframe:
			if len(files)>1: raise Exception('Support for multiple multi-frame DICOM files not implemented yet!')
			for frame in range(len(ds[tagDict['Frame sequence']].value)):
				frameList.append([file]+[frame]+[getAttribute(ds,attr,frame) for attr in reqAttributes])
		else: # Single frame DICOM files
			frameList.append([file]+[None]+[getAttribute(ds,attr) for attr in reqAttributes])
	frameList.sort(key=lambda tags: tags[2]) #First, sort on type (M/P/R/I)
	frameList.sort(key=lambda tags: tags[3]) #Second, sort on echo time
	frameList.sort(key=lambda tags: tags[4]) #Third, sort on slice location
	
	type = getType(frameList,True)
	dPar.dx=float(frameList[0][8][1])
	dPar.dy=float(frameList[0][8][0])
	dPar.dz=float(frameList[0][9])
	
	dPar.B0 = frameList[0][5]/gyro
	echoTimes = sorted(set([float(tags[3])/1000. for tags in frameList])) # [msec]->[sec]
	dPar.totalN = len(echoTimes)
	if not 'echoes' in dPar: dPar.echoes = range(dPar.totalN)
	echoTimes = [echoTimes[echo] for echo in dPar.echoes]
	dPar.N = len(dPar.echoes)
	dPar.t1 = echoTimes[0]
	dPar.dt = np.mean(np.diff(echoTimes))
	if np.max(np.diff(echoTimes))/dPar.dt>1.05 or np.min(np.diff(echoTimes))/dPar.dt<.95:
		print('Warning: echo inter-spacing varies more than 5%')
		print(echoTimes)
	nSlices = len(set([tags[4] for tags in frameList]))
	if not 'sliceList' in dPar: dPar.sliceList = range(nSlices)
	
	dPar.nx = frameList[0][6]
	dPar.ny = frameList[0][7]
	dPar.nz = len(dPar.sliceList)
	
	img = []
	if multiframe:
		file = frameList[0][0]
		dcm = dicom.read_file(file)
	for n in dPar.echoes:
		for slice in dPar.sliceList:
			i=(dPar.N*slice+n)*len(type)
			if type=='MP': #Magnitude/phase images
				magnFrame = i
				phaseFrame = i+1
				if multiframe:
					magn = dcm.pixel_array[frameList[magnFrame][1]].flatten()
					phase = dcm.pixel_array[frameList[phaseFrame][1]].flatten()
					reScaleIntercept=np.abs(getAttribute(dcm,'Rescale Intercept',frameList[phaseFrame][1])) #Absolute value needed for Siemens data to get correct phase sign
				else:
					magnFile = frameList[magnFrame][0]
					phaseFile = frameList[phaseFrame][0]
					mDcm = dicom.read_file(magnFile)
					pDcm = dicom.read_file(phaseFile)
					magn = mDcm.pixel_array.flatten()
					phase = pDcm.pixel_array.flatten()
					reScaleIntercept=np.abs(getAttribute(pDcm,'Rescale Intercept')) #Absolute value needed for Siemens data to get correct phase sign
				c=magn*np.exp(phase/float(reScaleIntercept)*2*np.pi*1j) #For some reason, intercept is used as slope (Siemens only?)
			elif type=='RI' or type=='MRI': #Real/imaginary images and Magnitude/real/imaginary images
				if type=='RI': realFrame = i+1
				elif type=='MRI': realFrame = i+2
				imagFrame = i
				if multiframe:
					realPart = dcm.pixel_array[frameList[realFrame][1]].flatten()
					imagPart = dcm.pixel_array[frameList[imagFrame][1]].flatten()
					reScaleIntercept=getAttribute(dcm,'Rescale Intercept',frameList[realFrame][1]) #Assumes real and imaginary slope/intercept are equal
					reScaleSlope=getAttribute(dcm,'Rescale Slope',frameList[realFrame][1])
				else:
					realFile = frameList[realFrame][0]
					imagFile = frameList[imagFrame][0]
					rDcm = dicom.read_file(realFile)
					iDcm = dicom.read_file(imagFile)
					realPart = rDcm.pixel_array.flatten()
					imagPart = iDcm.pixel_array.flatten()
					reScaleIntercept=getAttribute(rDcm,'Rescale Intercept') #Assumes real and imaginary slope/intercept are equal
					reScaleSlope=getAttribute(rDcm,'Rescale Slope')
				offset=reScaleIntercept/reScaleSlope
				c=(realPart+offset)+1.0*1j*(imagPart+offset)
			else: raise Exception('Unknown image types')
			img.append(c)
	dPar.frameList = frameList
	dPar.img = np.array(img)*dPar.reScale

# update dPar with information retrieved from MATLAB file arranged according to ISMRM fat-water toolbox
def updateDataParamsMATLAB(dPar,file):
	try: mat = scipy.io.loadmat(file)
	except: raise Exception('Could not read MATLAB file {}'.format(file))
	data = mat['imDataParams'][0,0]

	for i in range(0,4):
		if len(data[i].shape)==5:
			img = data[i] #Image data (row,col,slice,coil,echo)
		elif data[i].shape[1]>2:
			echoTimes = data[i][0] #TEs [sec]
		else:
			if data[i][0,0]>1: dPar.B0 = data[i][0,0] #Fieldstrength [T]
			else: clockwise = data[i][0,0] #Clockwiseprecession?

	if clockwise!=1: 
		raise Exception('Warning: Not clockwise precession. Need to write code to handle this case!')
	
	dPar.ny,dPar.nx,dPar.nz,nCoils,dPar.N = img.shape
	if nCoils>1: raise Exception('Warning: more than one coil. Need to write code to coil combine!')
	
	# Get only slices in dPar.sliceList
	if not 'sliceList' in dPar: dPar.sliceList = range(dPar.nz)
	else:
		img = img[:,:,dPar.sliceList,:,:]
		dPar.nz = len(dPar.sliceList)
	# Get only echoes in dPar.echoes
	dPar.totalN = dPar.N
	if not hasattr(dPar,'echoes'): dPar.echoes = range(dPar.totalN)
	else:
		img = img[:,:,:,:,dPar.echoes]
		echoTimes = echoTimes[dPar.echoes]
		dPar.N = len(dPar.echoes)
		
	dPar.t1 = echoTimes[0]
	dPar.dt = np.mean(np.diff(echoTimes))
	if np.max(np.diff(echoTimes))/dPar.dt>1.05 or np.min(np.diff(echoTimes))/dPar.dt<.95: 
		raise Exception('Warning: echo inter-spacing varies more than 5%')
	
	dPar.frameList = []
	
	dPar.dx,dPar.dy,dPar.dz = 1.5,1.5,5 #Ad hoc assumption on voxelsize
	
	# To get data as: (echo,slice,row,col)
	img.shape = (dPar.ny,dPar.nx,dPar.nz,dPar.N)
	img = np.transpose(img)
	img = np.swapaxes(img,2,3)
	
	img = img.flatten()
	dPar.img = img*dPar.reScale

# Get relative weights alpha of fat resonances based on CL, UD, and PUD per UD
def getFACalphas(CL=None,P2U=None,UD=None):
	P = 11 # Expects one water and ten triglyceride resonances
	M = [CL,UD,P2U].count(None)+2
	alpha = np.zeros([M,P],dtype=np.float32)
	alpha[0,0]=1. # Water component
	if M==2:
		# // F = 9A+(6(CL-4)+UD(2P2U-8))B+6C+4UDD+6E+2UDP2UF+2G+2H+I+UD(2P2U+2)J
		alpha[1,1:]=[9,6*(CL-4)+UD*(2*P2U-8),6,4*UD,6,2*UD*P2U,2,2,1,UD*(2*P2U+2)]
	elif M==3:
		# // F1 = 9A+6(CL-4)B+6C+6E+2G+2H+I
		# // F2 = (2P2U-8)B+4D+2P2UF+(2P2U+2)J
		alpha[1,1:]=[9,6*(CL-4),6,0,6,0,2,2,1,0]
		alpha[2,1:]=[0,2*P2U-8,0,4,0,2*P2U,0,0,0,2*P2U+2]
	elif M==4:
		# // F1 = 9A+6(CL-4)B+6C+6E+2G+2H+I
		# // F2 = -8B+4D+2J
		# // F3 = 2B+2F+2J
		alpha[1,1:]=[9,6*(CL-4),6,0,6,0,2,2,1,0]
		alpha[2,1:]=[0,-8,0,4,0,0,0,0,0,2]
		alpha[3,1:]=[0,2,0,0,0,2,0,0,0,2]
	elif M==5:
		# // F1 = 9A+6C+6E+2G+2H+I
		# // F2 = 2B
		# // F3 = 4D+2J
		# // F4 = 2F+2J
		alpha[1,1:]=[9,0,6,0,6,0,2,2,1,0]
		alpha[2,1:]=[0,2,0,0,0,0,0,0,0,0]
		alpha[3,1:]=[0,0,0,4,0,0,0,0,0,2]
		alpha[4,1:]=[0,0,0,0,0,2,0,0,0,2]
	return alpha

# Update model parameter object mPar and set default parameters
def updateModelParams(mPar):
	if 'watcs' in mPar: watCS = [float(mPar.watcs)]
	else: watCS = [4.7]
	if 'fatcs' in mPar: fatCS=[float(cs) for cs in mPar.fatcs.split(',')]
	else: fatCS=[1.3]
	mPar.CS = np.array(watCS+fatCS,dtype=np.float32)
	mPar.P = len(mPar.CS)
	if 'nfac' in mPar: mPar.nFAC = int(mPar.nfac)		
	else: mPar.nFAC = 0
	if mPar.nFAC>0 and mPar.P is not 11: raise Exception('FAC excpects exactly one water and ten triglyceride resonances')
	mPar.M = 2+mPar.nFAC
	
	if mPar.nFAC in [1,2]:
		if 'cl' in mPar: mPar.CL = float(mPar.cl)
		else: mPar.CL = 17.4
	if mPar.nFAC==1:
		if 'p2u' in mPar: mPar.P2U = float(mPar.p2u)
		else: mPar.p2u = 0.2
	if mPar.nFAC==0:
		mPar.alpha = np.zeros([mPar.M,mPar.P],dtype=np.float32)
		mPar.alpha[0,0]=1.
		if 'relamps' in mPar: 
			for (p,a) in enumerate(mPar.relamps.split(',')): 
				mPar.alpha[1,p+1]=float(a) 
		else: 
			for p in range(mPar.P): 
				mPar.alpha[1,p] = float(1/len(fatCS))
	elif mPar.nFAC==1:
		mPar.alpha = getFACalphas(mPar.CL,mPar.P2U)
	elif mPar.nFAC==2:
		mPar.alpha = getFACalphas(mPar.CL)
	elif mPar.nFAC==3:
		mPar.alpha = getFACalphas()
	else:
		raise Exception('Unknown number of FAC parameters: {}'.format(mPar.nFAC))
		
# Update algorithm parameter object aPar and set default parameters
def updateAlgoParams(aPar):
	if 'nr2' in aPar: aPar.nR2 = int(aPar.nr2)
	else: aPar.nR2 = 1
	if 'r2max' in aPar: aPar.R2max = float(aPar.r2max)
	else: aPar.R2max = 100.
	if 'r2cand' in aPar: aPar.R2cand = [float(R2) for R2 in aPar.r2cand.split(',')]
	else: aPar.R2cand = [0.]
	if 'fibsearch' in aPar: aPar.FibSearch = aPar.fibsearch=='True'
	else: aPar.FibSearch = False
	if 'mu' in aPar: aPar.mu = float(aPar.mu)
	else: aPar.mu = 1.
	if 'nb0' in aPar: aPar.nB0 = int(aPar.nb0)
	else: aPar.nB0 = 100
	if 'nicmiter' in aPar: aPar.nICMiter = int(aPar.nicmiter)
	else: aPar.nICMiter = 0
	if 'graphcut' in aPar: aPar.graphcut = aPar.graphcut == 'True'
	else: aPar.graphcut = False
	aPar.graphcutLevel = 100*(not aPar.graphcut) # Set graphcutlevel to 0 (cut) or 100 (no cut)
	if 'multiscale' in aPar: aPar.multiScale = aPar.multiscale == 'True'
	else: aPar.multiScale = False
	if 'use3d' in aPar: aPar.use3D = aPar.use3d == 'True'
	else: aPar.use3D = False

	if aPar.nR2>1: aPar.R2step = aPar.R2max/(aPar.nR2-1) #[sec-1]
	else: aPar.R2step = 1.0 #[sec-1]
	aPar.iR2cand=np.array(list(set([min(aPar.nR2-1,int(R2/aPar.R2step)) for R2 in aPar.R2cand]))) #[msec]
	aPar.nR2cand = len(aPar.iR2cand)
	aPar.maxICMupdate = round(aPar.nB0/10)
	
# Update data parameter object dPar, set default parameters and read data from files
def updateDataParams(dPar,outDir=None):
	if outDir: dPar.outDir = outDir
	elif 'outdir' in dPar: dPar.outDir = dPar.outdir
	else: raise Exception('No outDir defined')
	#Rescaling might be necessary for datasets with too small or large pixel values
	if 'rescale' in dPar: dPar.reScale = float(dPar.rescale)
	else: dPar.reScale = 1.0
	if 'echoes' in dPar:  dPar.echoes=[int(a) for a in dPar.echoes.split(',')]
	if 'slicelist' in dPar:  dPar.sliceList=[int(a) for a in dPar.slicelist.split(',')]
	if 'temp' in dPar: dPar.Temp = float(dPar.temp)
	if 'files' in dPar: 
		dPar.files = dPar.files.split(',')
		validFiles = getValidFiles(dPar['files'])
		if not validFiles: 
			if len(dPar.files)==1 and dPar.files[0][-4:]=='.mat':
				updateDataParamsMATLAB(dPar,dPar.files[0])
			else: raise Exception('No valid files found')
	elif 'dirs' in dPar:
		dPar.dirs = dPar.dirs.split(',')
		validFiles = getValidFiles(getFiles(dPar.dirs))
		if validFiles: updateDataParamsDICOM(dPar,validFiles)
		else: raise Exception('No valid files found')
	else:
		raise Exception('No "files" or "dirs" found in dataParams config file')

# extract data parameter object representing a single slice
def getSliceDataParams(dPar,slice,z):
	sliceDataParams = AttrDict(dPar)
	sliceDataParams.sliceList = [slice]	
	sliceDataParams.img = dPar.img.reshape(dPar.N,dPar.nz,dPar.ny*dPar.nx)[:,z,:].flatten()
	sliceDataParams.nz = 1	
	return sliceDataParams

# Configure the fat-water separation function from the c++ DLL
def init_FWcpp():
	DLLdir = r'.'
	try: lib = np.ctypeslib.load_library('FWQPBO', DLLdir)
	except: raise Exception('FW.dll not found in dir "{}"'.format(DLLdir))
	FWcpp = lib[1] # Does not work to access the function by name: lib.fwqpbo
	FWcpp.restype = None # Needed for void functions
	
	FWcpp.argtypes = [	np.ctypeslib.ndpointer(IMGTYPE, flags='aligned, contiguous'),
						np.ctypeslib.ndpointer(IMGTYPE, flags='aligned, contiguous'),
						ctypes.c_int,
						ctypes.c_int,
						ctypes.c_int,
						ctypes.c_int,
						ctypes.c_float,
						ctypes.c_float,
						ctypes.c_float,
						ctypes.c_float,
						ctypes.c_float,
						ctypes.c_float,
						np.ctypeslib.ndpointer(ctypes.c_float, flags='aligned, contiguous'),
						np.ctypeslib.ndpointer(ctypes.c_float, flags='aligned, contiguous'),
						ctypes.c_int,
						ctypes.c_int,
						ctypes.c_float,
						ctypes.c_int,
						np.ctypeslib.ndpointer(ctypes.c_int, flags='aligned, contiguous'),
						ctypes.c_int,
						ctypes.c_bool,
						ctypes.c_float,
						ctypes.c_int,
						ctypes.c_int,
						ctypes.c_int,
						ctypes.c_int,
						ctypes.c_bool,
						np.ctypeslib.ndpointer(IMGTYPE, flags='aligned, contiguous'),
						np.ctypeslib.ndpointer(IMGTYPE, flags='aligned, contiguous'),
						np.ctypeslib.ndpointer(IMGTYPE, flags='aligned, contiguous'),
						np.ctypeslib.ndpointer(IMGTYPE, flags='aligned, contiguous')]
	return FWcpp

# Get the total fat component (needed for Fatty Acid Composition, trivial otherwise)
def getFat(X,nVxl,alpha):
	fat = np.zeros(nVxl)+1j*np.zeros(nVxl)
	for m in range(1,alpha.shape[0]):
		fat += sum(alpha[m,1:])*X[m*nVxl:(m+1)*nVxl]
	return fat

# The core function: Allocate image matrices, call the DLL function, and save the images
def processDataset(dPar,aPar,mPar):
	if 'Temp' in dPar: mPar.CS[0] = 1.3+3.748-.01085*dPar.Temp # Temperature dependence according to Hernando 2014
	
	nVxl = dPar.nx*dPar.ny*dPar.nz
	
	Xreal = np.empty(nVxl*mPar.M,dtype=IMGTYPE)
	Ximag = np.empty(nVxl*mPar.M,dtype=IMGTYPE)
	R2map = np.empty(nVxl*(aPar.nR2>1),dtype=IMGTYPE)
	B0map = np.empty(nVxl,dtype=IMGTYPE)
	
	FWcpp = init_FWcpp()
	
	Yreal = np.real(dPar.img).astype(IMGTYPE)
	Yimag = np.imag(dPar.img).astype(IMGTYPE)
	
	if mPar.nFAC>0: # For Fatty Acid Composition
		# First pass: use standard fat-water separation to determine B0 and R2*
		FACalpha = mPar.alpha
		FACM = mPar.M
		mPar.UD = 2.6 # Derived from Lundbom 2010
		mPar.alpha = getFACalphas(mPar.CL,mPar.P2U,mPar.UD)
		mPar.M = mPar.alpha.shape[0]
	FWcpp(Yreal,Yimag,dPar.N,dPar.nx,dPar.ny,dPar.nz,dPar.dx,dPar.dy,dPar.dz,dPar.t1,dPar.dt,dPar.B0,mPar.CS,mPar.alpha.flatten(),mPar.M,mPar.P,aPar.R2step,aPar.nR2,aPar.iR2cand,aPar.nR2cand,aPar.FibSearch,aPar.mu,aPar.nB0,aPar.nICMiter,aPar.maxICMupdate,aPar.graphcutLevel,aPar.multiScale,Xreal,Ximag,R2map,B0map)
	X = Xreal[:nVxl*mPar.M]+1j*Ximag[:nVxl*mPar.M]
	eps = sys.float_info.epsilon
	wat = X[0*nVxl:1*nVxl]
	fat = getFat(X,nVxl,mPar.alpha)
	#TODO: add magnitude discrimination alternative
	ff = np.abs(fat[:])/(np.abs(wat[:])+np.abs(fat[:])+eps)
	
	if mPar.nFAC>0: # For Fatty Acid Composition
		#Re-calculate water and all fat components with FAC using the same B0- and R2*-maps
		mPar.alpha = FACalpha
		mPar.M = FACM
		FWcpp(Yreal,Yimag,dPar.N,dPar.nx,dPar.ny,dPar.nz,dPar.dx,dPar.dy,dPar.dz,dPar.t1,dPar.dt,dPar.B0,mPar.CS,mPar.alpha.flatten(),mPar.M,mPar.P,aPar.R2step,-aPar.nR2,aPar.iR2cand,aPar.nR2cand,aPar.FibSearch,aPar.mu,aPar.nB0,0,aPar.maxICMupdate,100,aPar.multiScale,Xreal,Ximag,R2map,B0map)	
		X = Xreal+1j*Ximag
		if mPar.nFAC==1:	
			# UD = F2/F1
			UD = np.abs(X[2*nVxl:3*nVxl]/(X[1*nVxl:2*nVxl]+eps))
		elif mPar.nFAC==2:	
			# UD = (F2+F3)/F1
			# PUD = F3/F1
			UD = np.abs((X[2*nVxl:3*nVxl]+X[3*nVxl:4*nVxl])/(X[1*nVxl:2*nVxl]+eps))
			PUD = np.abs((X[3*nVxl:4*nVxl])/(X[1*nVxl:2*nVxl]+eps))
		elif mPar.nFAC==3:
			# CL = 4+(F2+4F3+3F4)/3F1
			# UD = (F3+F4)/F1
			# PUD = F4/F1
			CL = 4 + np.abs((X[2*nVxl:3*nVxl]+4*X[3*nVxl:4*nVxl]+3*X[4*nVxl:5*nVxl])/(3*X[1*nVxl:2*nVxl]+eps))
			UD = np.abs((X[3*nVxl:4*nVxl]+X[4*nVxl:5*nVxl])/(X[1*nVxl:2*nVxl]+eps))
			PUD = np.abs((X[4*nVxl:5*nVxl])/(X[1*nVxl:2*nVxl]+eps))
		
	# Images to be saved:
	bwatfat = True # Water-only and fat-only
	bipop = False # Synthetic in-phase and opposed-phase
	bff = True # Fat fraction
	bB0map = True # B0 off-resonance field map
	
	shiftB0map = False # Shift the B0-map with half a period
	if shiftB0map:
		Omega = 1.0/dPar.dt/gyro/dPar.B0
		B0map += Omega/2
		B0map[B0map>Omega] -= Omega
	
	if not os.path.isdir(dPar.outDir): os.mkdir(dPar.outDir)
	if (bwatfat): save(dPar.outDir+r'/wat',np.abs(wat),dPar,0.,1.,'Water-only',101)
	if (bwatfat): save(dPar.outDir+r'/fat',np.abs(fat),dPar,0.,1.,'Fat-only',102)
	if (bipop): save(dPar.outDir+r'/ip',np.abs(wat+fat),dPar,0.,1.,'In-phase',103)
	if (bipop): save(dPar.outDir+r'/op',np.abs(wat-fat),dPar,0.,1.,'Opposed-phase',104)
	if (bff): save(dPar.outDir+r'/ff',ff,dPar,0.,1/1000,'Fat Fraction',105)
	if (aPar.nR2>1): save(dPar.outDir+r'/R2map',R2map,dPar,0.,1.0,'R2*',106)
	if (bB0map): save(dPar.outDir+r'/B0map',B0map,dPar,0.,1/1000,'Off-resonance (ppb)',107)
	if (mPar.nFAC>2): save(dPar.outDir+r'/CL',CL,dPar,0.,1/100,'FAC Chain length (1/100)',108)
	if (mPar.nFAC>0): save(dPar.outDir+r'/UD',UD,dPar,0.,1/100,'FAC Unsaturation degree (1/100)',109)
	if (mPar.nFAC>1): save(dPar.outDir+r'/PUD',PUD,dPar,0.,1/100,'FAC Polyunsaturation degree (1/100)',110)

# Read configuration file
def readConfig(file,section):
	config = configparser.ConfigParser()
	config.read(file)
	return AttrDict(config[section])

# Wrapper function
def FW(dataParamFile,algoParamFile,modelParamFile,outDir=None):
	# Read configuration files
	dataParams = readConfig(dataParamFile,'data parameters')
	algoParams = readConfig(algoParamFile,'algorithm parameters')
	modelParams = readConfig(modelParamFile,'model parameters')
	
	# Self-update configuration objects
	updateDataParams(dataParams,outDir)
	updateAlgoParams(algoParams)
	updateModelParams(modelParams)
	
	print('B0 = {}'.format(round(dataParams.B0,2)))
	print('N = {}'.format(dataParams.N))
	print('t1/dt = {}/{} msec'.format(round(dataParams.t1*1000,2),round(dataParams.dt*1000,2)))
	print('nx,ny,nz = {},{},{}'.format(dataParams.nx,dataParams.ny,dataParams.nz))
	print('dx,dy,dz = {},{},{}'.format(round(dataParams.dx,2),round(dataParams.dy,2),round(dataParams.dz,2)))
	
	# Run fat/water processing
	if algoParams.use3D or len(dataParams.sliceList)==1:
		processDataset(dataParams,algoParams,modelParams)
	else:
		for z,slice in enumerate(dataParams.sliceList):
			print('Processing slice {} ({}/{})...'.format(slice+1,z+1,len(dataParams.sliceList)))
			sliceDataParams = getSliceDataParams(dataParams,slice,z)
			processDataset(sliceDataParams,algoParams,modelParams)

# Command-line tool
def main():
	# Initiate command line parser
	p = optparse.OptionParser()
	p.add_option('--dataParamFile', '-d', default='',  type="string", help="Name of data parameter configuration text file")
	p.add_option('--algoParamFile', '-a', default='',  type="string", help="Name of algorithm parameter configuration text file")
	p.add_option('--modelParamFile', '-m', default='',  type="string", help="Name of model parameter configuration text file")
	
	# Parse command line
	options, arguments = p.parse_args()
	
	FW(options.dataParamFile,options.algoParamFile,options.modelParamFile)
	
if __name__ == '__main__': main()